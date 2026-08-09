from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import secrets
from typing import Callable, Protocol, Sequence

from .config import ConfigError
from .credentials import CredentialError, CredentialsResolver
from .models import BaselineSource, Config, SessionResult, StopReason
from .paths import compute_writable_py_files
from .test_manifest import (
    ExistingTestDiscovery,
    FrozenTestManifest,
    ManifestEntry,
    ManifestError,
    discover_existing_tests,
    manifest_entry_from_path,
    _manifest_hash,
)
from .testprep.service import (
    PreparationRequest,
    PreparationResult,
    PreparationSummary,
    TestPreparationService,
)
from .testprep.workspace import CandidateWorkspace
from .testrunner import TestRunResult


class V2RunnerFactory(Protocol):
    """Build a runner bound to the supplied pytest scope.

    V2 setup factories must accept both keyword arguments and return a runner
    exposing matching ``target_paths`` and ``allow_empty`` attributes.  The
    adapter below validates that contract before the runner can execute.
    """

    def __call__(
        self,
        project_root: Path,
        pytest_args: list[str],
        *,
        target_paths: tuple[str, ...],
        allow_empty: bool,
    ) -> object:
        ...


RunnerFactory = V2RunnerFactory
ConfigLoader = Callable[..., Config]
PreparationFactory = Callable[[PreparationRequest], PreparationResult]
ManifestFactory = Callable[
    [Path, Sequence[ManifestEntry], BaselineSource, int], FrozenTestManifest
]


@dataclass(frozen=True)
class SetupResult:
    config: Config | None
    manifest: FrozenTestManifest | None
    baseline: TestRunResult | None
    writable_paths: frozenset[Path]
    preparation_summary: PreparationSummary | None
    early_stop: SessionResult | None = None
    failure_detail: str | None = None

    @property
    def test_manifest(self) -> FrozenTestManifest | None:
        return self.manifest

    @property
    def preparation(self) -> PreparationSummary | None:
        return self.preparation_summary


class SessionSetup:
    """Own discovery, preparation, manifest freeze, and formal baseline."""

    def __init__(
        self,
        project_root: str | Path,
        config_loader: ConfigLoader,
        credentials: CredentialsResolver,
        existing_runner_factory: RunnerFactory,
        preparation_factory: object = TestPreparationService,
        manifest_factory: ManifestFactory | None = None,
        *,
        test_client: object | None = None,
        review_client: object | None = None,
        approval_provider: object | None = None,
        event_sink: object | None = None,
        guidance: str = "",
        high_risk_confirmation: bool | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self._config_loader = config_loader
        self._credentials = credentials
        self._existing_runner_factory = existing_runner_factory
        self._preparation_factory = preparation_factory
        self._manifest_factory = manifest_factory or manifest_from_entries
        self._test_client = test_client
        self._review_client = review_client
        self._approval_provider = approval_provider
        self._event_sink = event_sink
        self._guidance = guidance
        self._high_risk_confirmation = high_risk_confirmation

    def prepare(self) -> SetupResult:
        if not self.project_root.is_dir():
            return self._early(None, None, None, (), StopReason.CONFIG_ERROR)
        try:
            config = self._config_loader(
                self.project_root, {}, require_llm=True
            )
            self._credentials.get()
            writable_paths = frozenset(
                compute_writable_py_files(
                    self.project_root, config.allowed_paths, config.excluded_paths
                )
            )
        except (ConfigError, CredentialError, ValueError) as error:
            return self._early(
                None, None, None, (), StopReason.CONFIG_ERROR, str(error)
            )

        try:
            discovery_runner = runner_for(
                self._existing_runner_factory,
                self.project_root,
                config.pytest_args,
                allow_empty=True,
            )
            discovery = discover_existing_tests(self.project_root, discovery_runner)
        except (OSError, TypeError, ValueError, AttributeError) as error:
            return self._early(
                config, None, None, writable_paths, StopReason.ERROR, str(error)
            )
        if not discovery.result.valid:
            reason = (
                StopReason.ERROR
                if discovery.result.exit_code == 3
                else StopReason.CONFIG_ERROR
            )
            return self._early(
                config,
                None,
                None,
                writable_paths,
                reason,
                "pytest 未能成功发现现有测试。",
            )

        source = BaselineSource(config.baseline_source)

        try:
            preparation = self._prepare(config, discovery)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            return self._early(
                config,
                None,
                None,
                writable_paths,
                StopReason.TEST_PREPARATION_ERROR,
                str(error),
            )
        if preparation.stop_reason is not None:
            return self._early(
                config,
                None,
                preparation.summary,
                writable_paths,
                preparation.stop_reason,
                _preparation_failure_detail(preparation),
            )
        if not preparation.manifest_entries:
            reason = (
                StopReason.TEST_PREPARATION_ERROR
                if source in {BaselineSource.GENERATED, BaselineSource.MIXED}
                else StopReason.CONFIG_ERROR
            )
            return self._early(
                config,
                None,
                preparation.summary,
                writable_paths,
                reason,
                _preparation_failure_detail(preparation),
            )
        if discovery.collected_count:
            existing_paths = {path for path in discovery.test_paths}
            manifest_paths = {entry.path for entry in preparation.manifest_entries}
            if not existing_paths.issubset(manifest_paths):
                return self._early(
                    config,
                    None,
                    preparation.summary,
                    writable_paths,
                    StopReason.CONFIG_ERROR,
                )

        try:
            manifest = self._manifest_factory(
                self.project_root,
                preparation.manifest_entries,
                source,
                config.stability_runs,
            )
            manifest.verify(self.project_root)
        except (ManifestError, OSError, TypeError, ValueError) as error:
            return self._early(
                config,
                None,
                preparation.summary,
                writable_paths,
                StopReason.CONFIG_ERROR,
                str(error),
            )

        try:
            baseline_runner = runner_for(
                self._existing_runner_factory,
                self.project_root,
                config.pytest_args,
                target_paths=tuple(entry.path for entry in manifest.entries),
            )
            baseline = baseline_runner.run()
        except (OSError, TypeError, ValueError, AttributeError) as error:
            return self._early(
                config,
                manifest,
                None,
                writable_paths,
                StopReason.ERROR,
                str(error),
            )
        if not baseline.valid or not _has_collected_tests(baseline):
            reason = (
                StopReason.ERROR
                if baseline.exit_code == 3
                else StopReason.CONFIG_ERROR
            )
            return self._early(
                config,
                manifest,
                None,
                writable_paths,
                reason,
                "pytest 未能成功运行冻结的测试清单。",
            )

        preparation = replace(
            preparation,
            summary=replace(
                preparation.summary,
                baseline_test_count=sum(
                    not case.is_collection_error for case in baseline.cases
                ),
            ),
        )

        early_stop = (
            SessionResult(stop_reason=StopReason.SUCCESS)
            if not baseline.failure_ids
            else None
        )
        return SetupResult(
            config=config,
            manifest=manifest,
            baseline=baseline,
            writable_paths=writable_paths,
            preparation_summary=preparation.summary,
            early_stop=early_stop,
        )

    def _prepare(
        self, config: Config, discovery: ExistingTestDiscovery
    ) -> PreparationResult:
        workspace = CandidateWorkspace(
            self.project_root,
            f"session-{secrets.token_hex(12)}",
        )
        request = PreparationRequest(
            project_root=self.project_root,
            existing_discovery=discovery,
            test_client=self._test_client,
            review_client=self._review_client,
            config=config,
            approval_provider=self._approval_provider,
            workspace=workspace,
            event_sink=self._event_sink,
            guidance=self._guidance,
            high_risk_confirmation=self._high_risk_confirmation,
        )
        service = self._preparation_factory
        if isinstance(service, type):
            service = service()
        prepare = getattr(service, "prepare", None)
        if callable(prepare):
            result = prepare(request)
        elif callable(service):
            result = service(request)
        else:
            raise TypeError("preparation_factory must provide prepare(request)")
        if not isinstance(result, PreparationResult):
            raise TypeError("preparation_factory returned an invalid result")
        return result

    @staticmethod
    def _early(
        config: Config | None,
        manifest: FrozenTestManifest | None,
        summary: PreparationSummary | None,
        writable_paths: frozenset[Path] | tuple[()],
        reason: StopReason,
        failure_detail: str | None = None,
    ) -> SetupResult:
        return SetupResult(
            config=config,
            manifest=manifest,
            baseline=None,
            writable_paths=frozenset(writable_paths),
            preparation_summary=summary,
            early_stop=SessionResult(stop_reason=reason),
            failure_detail=failure_detail,
        )


def manifest_from_entries(
    project_root: str | Path,
    entries: Sequence[ManifestEntry],
    baseline_source: BaselineSource,
    stability_runs: int,
) -> FrozenTestManifest:
    """Freeze prepared entries while refreshing trusted file hashes."""
    root = Path(project_root).resolve()
    frozen_entries = tuple(
        manifest_entry_from_path(
            root,
            entry.path,
            entry.origin,
            entry.candidate_id,
        )
        for entry in entries
    )
    manifest_hash = _manifest_hash(
        BaselineSource(baseline_source), frozen_entries, stability_runs
    )
    return FrozenTestManifest(
        session_id=f"session-{secrets.token_hex(12)}",
        baseline_source=BaselineSource(baseline_source),
        entries=tuple(sorted(frozen_entries, key=lambda item: item.path)),
        stability_runs=stability_runs,
        manifest_hash=manifest_hash,
    )


def runner_for(
    factory: RunnerFactory,
    project_root: Path,
    pytest_args: Sequence[str],
    *,
    target_paths: Sequence[str] = (),
    allow_empty: bool = False,
) -> object:
    expected_paths = tuple(str(path) for path in target_paths)
    runner = factory(
        project_root,
        list(pytest_args),
        target_paths=expected_paths,
        allow_empty=allow_empty,
    )
    actual_paths = getattr(runner, "target_paths", None)
    if actual_paths is None:
        raise TypeError("v2 runner must expose target_paths")
    if tuple(str(path) for path in actual_paths) != expected_paths:
        raise ValueError("v2 runner target_paths do not match requested scope")
    actual_allow_empty = getattr(runner, "allow_empty", None)
    if actual_allow_empty is not allow_empty:
        raise ValueError("v2 runner allow_empty does not match requested scope")
    return runner


def _preparation_failure_detail(preparation: PreparationResult) -> str:
    if not preparation.summary.candidate_records:
        return "测试准备结束，但没有可用的候选诊断信息。"
    reasons = [
        record.reason
        for record in preparation.summary.candidate_records
        if record.reason
    ]
    if not reasons:
        return "测试准备结束，但没有可用的候选诊断信息。"
    return reasons[-1]


def _has_collected_tests(result: TestRunResult) -> bool:
    return any(not case.is_collection_error for case in result.cases)
