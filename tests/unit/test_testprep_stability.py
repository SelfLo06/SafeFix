from pathlib import Path

import pytest

from safefix.junit import TestCaseResult as _TestCaseResult
from safefix.models import CandidateStatus
from safefix.testrunner import TestRunResult as _TestRunResult
from safefix.testprep.stability import CandidateEvaluation, CandidateRun, StabilityRunner


def result(*failure_ids: str, valid: bool = True) -> _TestRunResult:
    cases = tuple(
        _TestCaseResult(
            failure_id=failure_id,
            classname=failure_id.split("::", 1)[0],
            name=failure_id.split("::", 1)[-1],
            status="failed",
        )
        for failure_id in failure_ids
    )
    return _TestRunResult(
        exit_code=1 if failure_ids else 0,
        cases=cases,
        valid=valid,
    )


class ScriptedRunner:
    def __init__(self, results: list[_TestRunResult]):
        self.results = results
        self.paths: list[Path] = []

    def __call__(self, path: Path) -> _TestRunResult:
        self.paths.append(path)
        return self.results[len(self.paths) - 1]


def test_stable_pass_runs_exactly_configured_number_of_times():
    path = Path("session/c1.py")
    runner = ScriptedRunner([result(), result(), result()])

    evaluation = StabilityRunner(runner, stability_runs=3).evaluate(path)

    assert evaluation.status is CandidateStatus.PASS
    assert len(evaluation.runs) == 3
    assert runner.paths == [path, path, path]
    assert evaluation.stable_failure_ids == frozenset()


def test_stable_fail_preserves_failure_ids():
    path = Path("session/c1.py")
    runner = ScriptedRunner(
        [
            result("candidate::test_x"),
            result("candidate::test_x"),
            result("candidate::test_x"),
        ]
    )

    evaluation = StabilityRunner(runner, stability_runs=3).evaluate(path)

    assert evaluation.status is CandidateStatus.FAIL
    assert evaluation.stable_failure_ids == frozenset({"candidate::test_x"})
    assert all(isinstance(run, CandidateRun) for run in evaluation.runs)


def test_invalid_collection_or_infrastructure_run_is_error_not_flaky():
    path = Path("session/c1.py")
    runner = ScriptedRunner([result(), result("collection_error::c" , valid=False), result()])

    evaluation = StabilityRunner(runner, stability_runs=3).evaluate(path)

    assert evaluation.status is CandidateStatus.ERROR


def test_mismatched_failure_ids_are_flaky():
    runner = ScriptedRunner(
        [
            result("candidate::test_x"),
            result("candidate::test_y"),
            result("candidate::test_x"),
        ]
    )

    evaluation = StabilityRunner(runner, stability_runs=3).evaluate(Path("session/c1.py"))

    assert evaluation.status is CandidateStatus.FLAKY
    assert evaluation.stable_failure_ids == frozenset()


def test_valid_runs_that_disagree_between_pass_and_fail_are_flaky():
    runner = ScriptedRunner([result(), result("candidate::test_x"), result()])

    evaluation = StabilityRunner(runner, stability_runs=3).evaluate(Path("session/c1.py"))

    assert evaluation.status is CandidateStatus.FLAKY


def test_stability_run_count_must_be_positive_and_integer():
    with pytest.raises(ValueError, match="stability_runs"):
        StabilityRunner(lambda path: result(), stability_runs=0)
    with pytest.raises(ValueError, match="stability_runs"):
        StabilityRunner(lambda path: result(), stability_runs=True)
