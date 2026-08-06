from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
import shutil
import tempfile
from typing import Callable, Protocol, Sequence

from ..events import SessionEvent
from ..models import (
    AcceptanceMode,
    BaselineSource,
    CandidateStatus,
    Config,
    Phase,
    StopReason,
)
from ..review import ReviewResult
from ..test_manifest import ManifestError, ManifestEntry, manifest_entry_from_path
from ..testrunner import TestRunResult, TestRunner
from .acceptance import CandidateAcceptancePolicy
from .models import GeneratedTestCandidate
from .parser import CandidateParser, ParseError
from .rules import validate_candidate
from .stability import CandidateEvaluation, StabilityRunner


class TestModelClient(Protocol):
    def complete(self, prompt: str) -> str:
        """Generate one bounded candidate response."""


class ReviewModelClient(Protocol):
    def review(self, prompt: str) -> ReviewResult:
        """Review one candidate."""


class ExistingDiscovery(Protocol):
    collected_count: int


class CandidateWorkspaceBoundary(Protocol):
    session_root: Path

    def stage(self, candidate: GeneratedTestCandidate) -> Path:
        """Write a candidate into the isolated staging area."""

    def accepted_path(self, candidate: GeneratedTestCandidate) -> Path:
        """Return the isolated destination for an accepted candidate."""


class ApprovalBoundary(Protocol):
    def approve(self, action: object) -> bool:
        """Return whether a manually gated candidate is accepted."""


@dataclass(frozen=True)
class PreparationRequest:
    project_root: Path
    existing_discovery: ExistingDiscovery
    test_client: TestModelClient | None
    review_client: ReviewModelClient | None
    config: Config
    approval_provider: ApprovalBoundary | None
    workspace: CandidateWorkspaceBoundary
    event_sink: object | None = None
    guidance: str = ""
    high_risk_confirmation: bool | None = None


@dataclass(frozen=True)
class CandidateAcceptanceRecord:
    candidate_id: str
    basis: str
    status: CandidateStatus | None
    accepted: bool
    automatic: bool
    manual: bool
    reason: str
    review: ReviewResult | None = None


@dataclass(frozen=True)
class PreparationSummary:
    baseline_source: BaselineSource
    existing_test_count: int = 0
    generated_candidate_count: int = 0
    generated_accepted_count: int = 0
    generated_pass_accepted: int = 0
    generated_fail_accepted_manual: int = 0
    generated_fail_accepted_automatic: int = 0
    rejected_count: int = 0
    error_count: int = 0
    flaky_count: int = 0
    candidate_records: tuple[CandidateAcceptanceRecord, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "baseline_source", BaselineSource(self.baseline_source))
        object.__setattr__(self, "candidate_records", tuple(self.candidate_records))

    @property
    def accepted_count(self) -> int:
        return self.generated_accepted_count

    @property
    def generated_rejected(self) -> int:
        return self.rejected_count


@dataclass(frozen=True)
class PreparationResult:
    manifest_entries: tuple[ManifestEntry, ...]
    summary: PreparationSummary
    stop_reason: StopReason | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "manifest_entries", tuple(self.manifest_entries))


CandidateRunner = Callable[[Path], TestRunResult]


class TestPreparationService:
    """Coordinate candidate preparation without owning baseline or repair state."""

    def __init__(
        self,
        *,
        candidate_runner: CandidateRunner | None = None,
        parser: CandidateParser | None = None,
        acceptance_policy: CandidateAcceptancePolicy | None = None,
    ) -> None:
        self._candidate_runner = candidate_runner
        self._parser = parser if parser is not None else CandidateParser()
        self._acceptance = (
            acceptance_policy
            if acceptance_policy is not None
            else CandidateAcceptancePolicy()
        )
        self._event_sequence = 1

    def prepare(self, request: PreparationRequest) -> PreparationResult:
        """Prepare accepted generated tests and return pre-baseline manifest entries."""
        source = BaselineSource(request.config.baseline_source)
        existing_count = request.existing_discovery.collected_count
        wants_generation = source in {
            BaselineSource.GENERATED,
            BaselineSource.MIXED,
        }
        generation_enabled = request.config.generate_tests and wants_generation

        if wants_generation and source is BaselineSource.GENERATED and existing_count > 0:
            return self._result(
                request,
                source,
                existing_count,
                stop_reason=StopReason.CONFIG_ERROR,
            )
        try:
            existing_entries = self._existing_manifest_entries(request, source)
        except (ManifestError, OSError, TypeError, ValueError):
            return self._result(
                request,
                source,
                existing_count,
                stop_reason=StopReason.CONFIG_ERROR,
            )
        if generation_enabled and request.config.acceptance_mode is AcceptanceMode.HIGH_RISK:
            if request.high_risk_confirmation is not True:
                return self._result(
                    request,
                    source,
                    existing_count,
                    stop_reason=StopReason.CONFIG_ERROR,
                )

        if not generation_enabled:
            return self._result(
                request,
                source,
                existing_count,
                existing_entries=existing_entries,
            )

        try:
            result = self._prepare_generated(
                request, source, existing_count, existing_entries
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            # This is the deliberate preparation boundary: model, staging, and
            # review infrastructure failures stop preparation without inventing
            # candidates or allowing a partial formal manifest.
            result = self._result(
                request,
                source,
                existing_count,
                stop_reason=StopReason.TEST_PREPARATION_ERROR,
            )
        finally:
            try:
                self._close_test_model(request.test_client)
            except (OSError, RuntimeError, TypeError, ValueError):
                result = self._result(
                    request,
                    source,
                    existing_count,
                    stop_reason=StopReason.TEST_PREPARATION_ERROR,
                )
        return result

    def _prepare_generated(
        self,
        request: PreparationRequest,
        source: BaselineSource,
        existing_count: int,
        existing_entries: tuple[ManifestEntry, ...],
    ) -> PreparationResult:
        if request.test_client is None:
            raise RuntimeError("Test Model is required when test generation is enabled")
        response = request.test_client.complete(self._prompt(request))
        self._emit(request, "model-call", {"role": "test"})
        try:
            candidates = self._parser.parse(response)
        except ParseError as exc:
            summary = _SummaryBuilder(source, existing_count, 0)
            summary.rejected_count = 1
            summary.records.append(
                CandidateAcceptanceRecord(
                    candidate_id="<parse-error>",
                    basis="",
                    status=None,
                    accepted=False,
                    automatic=False,
                    manual=False,
                    reason=str(exc),
                )
            )
            return PreparationResult(
                self._manifest_entries(request, source, (), existing_entries),
                summary.freeze(),
            )
        summary = _SummaryBuilder(source, existing_count, len(candidates))
        accepted: list[tuple[GeneratedTestCandidate, CandidateAcceptanceRecord, Path]] = []
        runner = self._runner_for(request)
        stability = StabilityRunner(
            runner,
            request.config.stability_runs,
            request.workspace.session_root,
        )
        auto_failures = 0

        for candidate in candidates:
            violations = validate_candidate(candidate, request.project_root)
            if violations:
                summary.rejected_count += 1
                summary.records.append(
                    CandidateAcceptanceRecord(
                        candidate.candidate_id,
                        candidate.basis,
                        None,
                        False,
                        False,
                        False,
                        "; ".join(item.code for item in violations),
                    )
                )
                continue

            staged = request.workspace.stage(candidate)
            evaluation = stability.evaluate(staged)
            self._emit(
                request,
                "stability-run",
                {
                    "candidate_id": candidate.candidate_id,
                    "status": evaluation.status.value,
                },
            )
            if evaluation.status is CandidateStatus.ERROR:
                summary.error_count += 1
                self._record_rejected(summary, candidate, evaluation)
                continue
            if evaluation.status is CandidateStatus.FLAKY:
                summary.flaky_count += 1
                self._record_rejected(summary, candidate, evaluation)
                continue

            review_result = self._review_if_required(request, candidate, evaluation)
            decision = self._acceptance.decide(
                request.config.acceptance_mode,
                evaluation,
                review_result,
                existing_count,
                self._model_identity_pairs(request.config),
                auto_failures,
                request.config.max_auto_accepted_failures,
            )
            if decision.requires_manual:
                approved = (
                    request.approval_provider is not None
                    and request.approval_provider.approve(candidate)
                )
                if approved:
                    decision = replace(
                        decision,
                        accepted=True,
                        automatic=False,
                        reason="manual approval accepted",
                    )

            self._emit(
                request,
                "acceptance",
                {
                    "candidate_id": candidate.candidate_id,
                    "accepted": decision.accepted,
                    "automatic": decision.automatic,
                },
            )

            record = CandidateAcceptanceRecord(
                candidate.candidate_id,
                candidate.basis,
                evaluation.status,
                decision.accepted,
                decision.automatic,
                decision.requires_manual,
                decision.reason,
                review_result,
            )
            summary.records.append(record)
            if not decision.accepted:
                summary.rejected_count += 1
                continue

            accepted_path = request.workspace.accepted_path(candidate)
            accepted_path.parent.mkdir(parents=True, exist_ok=True)
            accepted_path.write_bytes(staged.read_bytes())
            accepted.append((candidate, record, accepted_path))
            summary.generated_accepted_count += 1
            if evaluation.status is CandidateStatus.PASS:
                summary.generated_pass_accepted += 1
            elif record.automatic:
                summary.generated_fail_accepted_automatic += 1
                auto_failures += 1
            else:
                summary.generated_fail_accepted_manual += 1

        entries = self._manifest_entries(request, source, accepted, existing_entries)
        return PreparationResult(entries, summary.freeze())

    def _review_if_required(
        self,
        request: PreparationRequest,
        candidate: GeneratedTestCandidate,
        evaluation: CandidateEvaluation,
    ) -> ReviewResult | None:
        if (
            request.config.acceptance_mode is not AcceptanceMode.HIGH_RISK
            or evaluation.status is not CandidateStatus.FAIL
            or request.review_client is None
        ):
            return None
        return request.review_client.review(
            f"Review candidate {candidate.candidate_id}: {candidate.basis}"
        )

    def _runner_for(self, request: PreparationRequest) -> CandidateRunner:
        if self._candidate_runner is not None:
            return self._candidate_runner
        workspace_runner = getattr(request.workspace, "run_candidate", None)
        if callable(workspace_runner):
            return workspace_runner
        return self._isolated_project_runner(request)

    @staticmethod
    def _isolated_project_runner(request: PreparationRequest) -> CandidateRunner:
        project_root = request.project_root.resolve()

        def run(candidate: Path) -> TestRunResult:
            candidate_path = Path(candidate).resolve()
            try:
                relative_candidate = candidate_path.relative_to(project_root)
            except ValueError as exc:
                raise ValueError("candidate path must be inside the project root") from exc
            with tempfile.TemporaryDirectory(prefix="safefix-candidate-project-") as root:
                snapshot = Path(root) / "project"
                shutil.copytree(project_root, snapshot)
                snapshot_candidate = snapshot / relative_candidate
                if not snapshot_candidate.is_file():
                    raise ValueError("candidate is missing from the project snapshot")
                return TestRunner(
                    snapshot,
                    pytest_args=request.config.pytest_args,
                    target_paths=(relative_candidate.as_posix(),),
                ).run()

        return run

    @staticmethod
    def _model_identity_pairs(config: Config) -> tuple[tuple[str, str], tuple[str, str]]:
        return (
            (config.test_base_url, config.test_model),
            (config.review_base_url, config.review_model),
        )

    @staticmethod
    def _record_rejected(
        summary: _SummaryBuilder,
        candidate: GeneratedTestCandidate,
        evaluation: CandidateEvaluation,
    ) -> None:
        summary.records.append(
            CandidateAcceptanceRecord(
                candidate.candidate_id,
                candidate.basis,
                evaluation.status,
                False,
                False,
                False,
                evaluation.reason,
            )
        )

    def _manifest_entries(
        self,
        request: PreparationRequest,
        source: BaselineSource,
        accepted: Sequence[tuple[GeneratedTestCandidate, CandidateAcceptanceRecord, Path]],
        existing_entries: Sequence[ManifestEntry] | None = None,
    ) -> tuple[ManifestEntry, ...]:
        entries: list[ManifestEntry] = []
        if source in {BaselineSource.EXISTING, BaselineSource.MIXED}:
            entries.extend(
                existing_entries
                if existing_entries is not None
                else self._existing_manifest_entries(request, source)
            )
        if source in {BaselineSource.GENERATED, BaselineSource.MIXED}:
            entries.extend(
                _entry(request.project_root, path, BaselineSource.GENERATED, candidate.candidate_id)
                for candidate, _, path in accepted
            )
        entries.sort(key=lambda entry: entry.path)
        return tuple(entries)

    def _existing_manifest_entries(
        self,
        request: PreparationRequest,
        source: BaselineSource,
    ) -> tuple[ManifestEntry, ...]:
        if source not in {BaselineSource.EXISTING, BaselineSource.MIXED}:
            return ()
        return tuple(
            _entry(request.project_root, path, BaselineSource.EXISTING)
            for path in self._existing_paths(request.existing_discovery)
        )

    @staticmethod
    def _existing_paths(
        discovery: ExistingDiscovery,
    ) -> tuple[str | Path, ...]:
        for name in ("test_paths", "existing_test_paths", "paths"):
            paths = getattr(discovery, name, None)
            if paths:
                return tuple(dict.fromkeys(paths))
        if discovery.collected_count == 0:
            return ()
        raise ManifestError(
            "existing discovery collected tests but did not provide their paths"
        )

    def _result(
        self,
        request: PreparationRequest,
        source: BaselineSource,
        existing_count: int,
        *,
        existing_entries: Sequence[ManifestEntry] | None = None,
        stop_reason: StopReason | None = None,
    ) -> PreparationResult:
        summary = PreparationSummary(
            baseline_source=source,
            existing_test_count=existing_count,
        )
        entries = (
            ()
            if stop_reason is not None
            else self._manifest_entries(request, source, (), existing_entries)
        )
        return PreparationResult(entries, summary, stop_reason)

    @staticmethod
    def _prompt(request: PreparationRequest) -> str:
        guidance = request.guidance.strip()
        return (
            "Generate bounded pytest candidates for public behavior. "
            "Return the required JSON candidate contract."
            + (f" Operator guidance: {guidance}" if guidance else "")
        )

    @staticmethod
    def _close_test_model(client: TestModelClient | None) -> None:
        if client is None:
            return
        close = getattr(client, "close", None)
        if callable(close):
            close()

    def _emit(
        self,
        request: PreparationRequest,
        kind: str,
        payload: dict[str, object],
    ) -> None:
        sink = request.event_sink
        if sink is None:
            return
        event = SessionEvent(
            sequence=self._event_sequence,
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            phase=Phase.TEST_PREPARATION,
            kind=kind,
            safe_payload=payload,
        )
        emit = getattr(sink, "emit", None)
        if callable(emit):
            emit(event)
        elif callable(sink):
            sink(event)
        else:
            raise TypeError("event_sink must provide emit(event)")
        self._event_sequence += 1


@dataclass
class _SummaryBuilder:
    baseline_source: BaselineSource
    existing_test_count: int
    generated_candidate_count: int
    generated_accepted_count: int = 0
    generated_pass_accepted: int = 0
    generated_fail_accepted_manual: int = 0
    generated_fail_accepted_automatic: int = 0
    rejected_count: int = 0
    error_count: int = 0
    flaky_count: int = 0
    records: list[CandidateAcceptanceRecord] = field(default_factory=list)

    def freeze(self) -> PreparationSummary:
        return PreparationSummary(
            baseline_source=self.baseline_source,
            existing_test_count=self.existing_test_count,
            generated_candidate_count=self.generated_candidate_count,
            generated_accepted_count=self.generated_accepted_count,
            generated_pass_accepted=self.generated_pass_accepted,
            generated_fail_accepted_manual=self.generated_fail_accepted_manual,
            generated_fail_accepted_automatic=self.generated_fail_accepted_automatic,
            rejected_count=self.rejected_count,
            error_count=self.error_count,
            flaky_count=self.flaky_count,
            candidate_records=tuple(self.records),
        )


def _entry(
    project_root: Path,
    path: str | Path,
    origin: BaselineSource,
    candidate_id: str | None = None,
) -> ManifestEntry:
    return manifest_entry_from_path(
        project_root,
        path,
        origin,
        candidate_id,
    )
