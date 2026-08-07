from dataclasses import dataclass
from pathlib import Path

import pytest

from safefix.junit import TestCaseResult as _TestCaseResult
from safefix.models import (
    AcceptanceMode,
    BaselineSource,
    CandidateStatus,
    Config,
    ReviewVerdict,
)
from safefix.review import ReviewResult
from safefix.session_setup import manifest_from_entries
from safefix.test_manifest import ManifestError
from safefix.testprep import PreparationRequest, TestPreparationService as _TestPreparationService
from safefix.testprep.workspace import CandidateWorkspace
from safefix.testrunner import TestRunResult as _TestRunResult


@dataclass(frozen=True)
class PreparationDemoManifest:
    existing_test_count: int
    generated_test_count: int
    paths: tuple[str, ...]


@dataclass(frozen=True)
class PreparationDemoResult:
    manifest: PreparationDemoManifest
    candidate_status: CandidateStatus | None
    existing_contents: str
    test_model_calls: int
    accepted: bool
    automatic: bool
    manual_approval_calls: int
    mutation_rejected: bool


def run_preparation_demo(
    tmp_path: Path,
    *,
    source: BaselineSource,
    acceptance_mode: AcceptanceMode = AcceptanceMode.STANDARD,
    candidate_result: _TestRunResult | None = None,
) -> PreparationDemoResult:
    (tmp_path / "tests").mkdir()
    existing = tmp_path / "tests" / "test_existing.py"
    existing.write_text("def test_existing():\n    assert True\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")

    class TestClient:
        calls = 0

        def complete(self, _prompt: str) -> str:
            self.calls += 1
            return (
                '{"candidates":[{"candidate_id":"generated",'
                '"test_source":"def test_generated():\\n    assert True\\n",'
                '"basis":"public behavior", "sources":["src/app.py"],'
                '"touched_existing_tests":[]}]}'
            )

        def close(self) -> None:
            return None

    class Discovery:
        collected_count = 1 if source in {BaselineSource.EXISTING, BaselineSource.MIXED} else 0
        test_paths = ("tests/test_existing.py",)

    class Approval:
        calls = 0

        def approve(self, _candidate: object) -> bool:
            self.calls += 1
            return True

    review = type(
        "ReviewClient",
        (),
        {"review": lambda _self, _prompt: ReviewResult(
            verdict=ReviewVerdict.PASS, basis_supported=True, invented_behavior=False,
            implementation_coupling=False, risk="low", summary="supported"
        )},
    )()
    config = Config(
        generate_tests=source is not BaselineSource.EXISTING,
        baseline_source=source,
        acceptance_mode=acceptance_mode,
        stability_runs=1,
        test_base_url="https://test.invalid/v1",
        test_model="test-model",
        review_base_url="https://review.invalid/v1",
        review_model="review-model",
    )
    request = PreparationRequest(
        project_root=tmp_path,
        existing_discovery=Discovery(),
        test_client=TestClient(),
        review_client=review,
        config=config,
        approval_provider=Approval(),
        workspace=CandidateWorkspace(tmp_path, "preparation-demo"),
        high_risk_confirmation=True,
    )
    client = request.test_client
    approval = request.approval_provider
    result = _TestPreparationService(
        candidate_runner=lambda _path: candidate_result or _TestRunResult(0, (), valid=True)
    ).prepare(request)
    assert result.stop_reason is None
    records = result.summary.candidate_records
    record = records[0] if records else None

    mutation_rejected = False
    if result.manifest_entries:
        frozen = manifest_from_entries(tmp_path, result.manifest_entries, source, 1)
        frozen.verify(tmp_path)
        frozen_path = tmp_path / frozen.entries[0].path
        frozen_path.write_text(frozen_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with pytest.raises(ManifestError):
            frozen.verify(tmp_path)
        mutation_rejected = True

    origins = [entry.origin for entry in result.manifest_entries]
    return PreparationDemoResult(
        manifest=PreparationDemoManifest(
            existing_test_count=origins.count(BaselineSource.EXISTING),
            generated_test_count=origins.count(BaselineSource.GENERATED),
            paths=tuple(entry.path for entry in result.manifest_entries),
        ),
        candidate_status=record.status if record else None,
        existing_contents=existing.read_text(encoding="utf-8"),
        test_model_calls=client.calls,
        accepted=record.accepted if record else False,
        automatic=record.automatic if record else False,
        manual_approval_calls=approval.calls,
        mutation_rejected=mutation_rejected,
    )


def test_mixed_preparation_keeps_existing_test_in_final_manifest(tmp_path: Path) -> None:
    result = run_preparation_demo(tmp_path, source=BaselineSource.MIXED)
    assert result.manifest.existing_test_count == 1
    assert result.manifest.generated_test_count == 1
    assert result.manifest.paths[0] == ".safefix/sessions/preparation-demo/accepted/generated.py"
    assert result.manifest.paths[1] == "tests/test_existing.py"
    assert result.test_model_calls == 1


def test_preparation_records_stable_acceptance_without_mutating_existing_test(tmp_path: Path) -> None:
    result = run_preparation_demo(tmp_path, source=BaselineSource.MIXED)
    assert result.candidate_status is CandidateStatus.PASS
    assert result.accepted is True
    assert result.automatic is True
    assert result.existing_contents == "def test_existing():\n    assert True\n"
    assert result.mutation_rejected is True


def test_existing_only_preparation_keeps_the_test_model_unused_after_freeze(tmp_path: Path) -> None:
    result = run_preparation_demo(tmp_path, source=BaselineSource.EXISTING)
    assert result.test_model_calls == 0
    assert result.manifest.generated_test_count == 0
    assert result.manifest.paths == ("tests/test_existing.py",)
    assert result.mutation_rejected is True


@pytest.mark.parametrize(
    ("mode", "status", "accepted", "automatic", "manual_calls"),
    [
        (AcceptanceMode.STANDARD, CandidateStatus.PASS, True, True, 0),
        (AcceptanceMode.STANDARD, CandidateStatus.FAIL, True, False, 1),
        (AcceptanceMode.HIGH_RISK, CandidateStatus.FAIL, True, True, 0),
    ],
)
def test_candidate_acceptance_comes_from_preparation_service(
    tmp_path: Path,
    mode: AcceptanceMode,
    status: CandidateStatus,
    accepted: bool,
    automatic: bool,
    manual_calls: int,
) -> None:
    failed = (_TestCaseResult("candidate::test_generated", "candidate", "test_generated", "failed"),)
    result = run_preparation_demo(
        tmp_path,
        source=BaselineSource.GENERATED,
        acceptance_mode=mode,
        candidate_result=_TestRunResult(0 if status is CandidateStatus.PASS else 1, () if status is CandidateStatus.PASS else failed, valid=True),
    )
    assert result.candidate_status is status
    assert result.accepted is accepted
    assert result.automatic is automatic
    assert result.manual_approval_calls == manual_calls
