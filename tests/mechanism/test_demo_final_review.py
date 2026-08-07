from pathlib import Path
import json

from safefix.models import AcceptanceMode, BaselineSource, ReviewVerdict, StopReason
from safefix.review import ReviewResult
from safefix.models import Config
from safefix.llm.mock import MockLLM
from safefix.runner import SessionRunner
from safefix.session_setup import manifest_from_entries
from safefix.test_manifest import ManifestEntry
from safefix.testprep.service import PreparationResult, PreparationSummary
from safefix.testrunner import TestRunResult as _TestRunResult
from safefix.junit import TestCaseResult as _TestCaseResult

def run_final_review_demo(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    source = tmp_path / "src" / "app.py"
    source.write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_app.py").write_text(
        "def test_app():\n    assert True\n", encoding="utf-8"
    )

    def report(*failure_ids: str) -> _TestRunResult:
        cases = tuple(_TestCaseResult(item, "tests.test_app", item, "failed") for item in failure_ids)
        if not cases:
            cases = (_TestCaseResult("tests.test_app::test_app", "tests.test_app", "test_app", "passed"),)
        return _TestRunResult(1 if failure_ids else 0, cases, valid=True)

    class Runner:
        def __init__(self, target_paths, allow_empty):
            self.target_paths = target_paths
            self.allow_empty = allow_empty
            self.reports = iter([
                report("tests.test_app::test_broken"),
                report(),
                report("tests.test_app::test_broken"),
                report(),
            ])

        def collect_test_paths(self):
            return ("tests/test_app.py",)

        def run(self):
            return _TestRunResult(0, (), valid=True) if not self.target_paths else next(self.reports)

    def preparation(_request):
        return PreparationResult(
            (ManifestEntry("tests/test_app.py", "prepared", BaselineSource.EXISTING),),
            PreparationSummary(BaselineSource.EXISTING),
        )

    class Review:
        def review(self, _prompt):
            return ReviewResult(ReviewVerdict.REVIEW_REQUIRED, True, False, False, "low", "needs gate")

    patch = json.dumps({
        "tool": "apply_patch",
        "changes": [{"path": "src/app.py", "old_text": "value = 1", "new_text": "value = 2"}],
    })

    def make(mode, approval):
        reports = iter([report("tests.test_app::test_broken"), report()])

        class SessionRunnerFactory:
            def __call__(self, _root, _args, *, target_paths, allow_empty):
                if not target_paths:
                    return Runner(target_paths, allow_empty)

                class EvaluationRunner:
                    def __init__(self):
                        self.target_paths = target_paths
                        self.allow_empty = allow_empty

                    def collect_test_paths(self):
                        return ("tests/test_app.py",)

                    def run(self):
                        return next(reports)

                return EvaluationRunner()

        return SessionRunner(
            tmp_path / mode.value,
            cli_overrides={"acceptance_mode": mode},
            credentials=type("Credentials", (), {"get": lambda _self: "key"})(),
            config_loader=lambda *_args, **_kwargs: Config(
                base_url="https://repair.invalid/v1", model="repair-model", acceptance_mode=mode
            ),
            test_runner_factory=SessionRunnerFactory(),
            llm_client=MockLLM([patch]),
            preparation_factory=preparation,
            manifest_factory=manifest_from_entries,
            final_review_client=Review(),
            approval=approval,
        )

    standard_root = tmp_path / AcceptanceMode.STANDARD.value
    high_root = tmp_path / AcceptanceMode.HIGH_RISK.value
    standard_root.mkdir(); high_root.mkdir()
    for root in (standard_root, high_root):
        (root / "src").mkdir(); (root / "tests").mkdir()
        (root / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
        (root / "tests" / "test_app.py").write_text("def test_app():\n    assert True\n", encoding="utf-8")
    standard = make(AcceptanceMode.STANDARD, None).run()
    high = make(AcceptanceMode.HIGH_RISK, type("Approval", (), {"approve": lambda _self, _request: False})()).run()
    standard_artifact = json.loads((standard_root / "safefix-session.json").read_text())
    high_artifact = json.loads((high_root / "safefix-session.json").read_text())
    return (
        type("ReviewDemoResult", (), {
            "stop_reason": standard.stop_reason,
            "artifact_warning": standard_artifact["review"]["warning"],
        })(),
        type("ReviewDemoResult", (), {
            "stop_reason": high.stop_reason,
            "restored_source": (high_root / "src" / "app.py").read_text(),
            "review_verdict": ReviewVerdict(high_artifact["review_verdict"]),
            "acceptance_mode": AcceptanceMode(high_artifact["acceptance_mode"]),
        })(),
    )


def test_standard_review_required_is_warning_but_high_risk_requires_gate(tmp_path: Path) -> None:
    standard, high_risk = run_final_review_demo(tmp_path)

    assert standard.stop_reason is StopReason.SUCCESS
    assert standard.artifact_warning is True
    assert high_risk.stop_reason is StopReason.FINAL_REVIEW_REJECTED
    assert high_risk.restored_source == "value = 1\n"
    assert high_risk.review_verdict is ReviewVerdict.REVIEW_REQUIRED
    assert high_risk.acceptance_mode is AcceptanceMode.HIGH_RISK
