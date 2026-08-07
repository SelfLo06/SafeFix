from pathlib import Path
from dataclasses import dataclass

from safefix.models import AcceptanceMode, BaselineSource, CandidateStatus, Config, ReviewVerdict
from safefix.testprep.acceptance import CandidateAcceptancePolicy
from safefix.testprep.models import GeneratedTestCandidate
from safefix.testprep.stability import CandidateEvaluation
from safefix.review import ReviewResult
from safefix.test_manifest import discover_existing_tests
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
    candidate_status: CandidateStatus
    existing_contents: str
    test_model_calls: int

def run_preparation_demo(tmp_path: Path, *, source: BaselineSource):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_existing.py").write_text(
        "def test_existing():\n    assert True\n", encoding="utf-8"
    )
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
        collected_count = 1
        test_paths = ("tests/test_existing.py",)

    def candidate_runner(_path: Path) -> _TestRunResult:
        return _TestRunResult(exit_code=0, cases=(), valid=True)

    request = PreparationRequest(
        project_root=tmp_path,
        existing_discovery=Discovery(),
        test_client=TestClient(),
        review_client=None,
        config=Config(
            generate_tests=True,
            baseline_source=source,
            acceptance_mode=AcceptanceMode.STANDARD,
            stability_runs=1,
        ),
        approval_provider=None,
        workspace=CandidateWorkspace(tmp_path, "preparation-demo"),
    )
    client = request.test_client
    result = _TestPreparationService(candidate_runner=candidate_runner).prepare(request)
    assert result.stop_reason is None
    origins = [entry.origin for entry in result.manifest_entries]
    return PreparationDemoResult(
        manifest=PreparationDemoManifest(
            existing_test_count=origins.count(BaselineSource.EXISTING),
            generated_test_count=origins.count(BaselineSource.GENERATED),
            paths=tuple(entry.path for entry in result.manifest_entries),
        ),
        candidate_status=CandidateStatus.PASS,
        existing_contents=(tmp_path / "tests" / "test_existing.py").read_text(
            encoding="utf-8"
        ),
        test_model_calls=client.calls,
    )


def test_mixed_preparation_keeps_existing_test_in_final_manifest(tmp_path: Path) -> None:
    result = run_preparation_demo(tmp_path, source=BaselineSource.MIXED)

    assert result.manifest.existing_test_count == 1
    assert result.manifest.generated_test_count == 1
    assert result.manifest.paths[0] == ".safefix/sessions/preparation-demo/accepted/generated.py"
    assert result.manifest.paths[1] == "tests/test_existing.py"
    assert result.test_model_calls == 1


def test_preparation_records_stable_acceptance_without_mutating_existing_test(
    tmp_path: Path,
) -> None:
    result = run_preparation_demo(tmp_path, source=BaselineSource.MIXED)

    assert result.candidate_status is CandidateStatus.PASS
    assert result.existing_contents == "def test_existing():\n    assert True\n"


def test_existing_only_preparation_keeps_the_test_model_unused_after_freeze(
    tmp_path: Path,
) -> None:
    result = run_preparation_demo(tmp_path, source=BaselineSource.EXISTING)

    assert result.test_model_calls == 0
    assert result.manifest.generated_test_count == 0
    assert result.manifest.paths == ("tests/test_existing.py",)


def test_candidate_acceptance_demonstrates_standard_and_high_risk_boundaries() -> None:
    candidate = GeneratedTestCandidate(
        candidate_id="candidate",
        test_source="def test_generated():\n    assert True\n",
        basis="public behavior",
        sources=("src/app.py",),
    )

    def evaluation(status: CandidateStatus) -> CandidateEvaluation:
        return CandidateEvaluation(
        candidate=Path("candidate.py"),
        status=status,
        runs=(),
        stable_failure_ids=(
            frozenset({"candidate::test_behavior"})
            if status is CandidateStatus.FAIL
            else frozenset()
        ),
        reason="stable",
    )

    review = ReviewResult(
        ReviewVerdict.PASS, True, False, False, "low", "supported"
    )
    policy = CandidateAcceptancePolicy()
    standard_pass = policy.decide(
        AcceptanceMode.STANDARD, evaluation(CandidateStatus.PASS), None, 1, (), 0, 3
    )
    standard_fail = policy.decide(
        AcceptanceMode.STANDARD, evaluation(CandidateStatus.FAIL), review, 1, (), 0, 3
    )
    high_risk_fail = policy.decide(
        AcceptanceMode.HIGH_RISK,
        evaluation(CandidateStatus.FAIL),
        review,
        0,
        (("https://test.invalid/v1", "test"), ("https://review.invalid/v1", "review")),
        0,
        3,
    )

    assert candidate.candidate_id == "candidate"
    assert standard_pass.accepted is True and standard_pass.automatic is True
    assert standard_fail.requires_manual is True and standard_fail.automatic is False
    assert high_risk_fail.accepted is True and high_risk_fail.automatic is True
