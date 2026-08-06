from pathlib import Path

import pytest

from safefix.junit import TestCaseResult as _TestCaseResult
from safefix.config import MAX_STABILITY_RUNS, ConfigError, load_config
from safefix.models import CandidateStatus
from safefix.testrunner import TestRunResult as _TestRunResult
from safefix.testprep.stability import CandidateEvaluation, CandidateRun, StabilityRunner
from safefix.testprep.models import GeneratedTestCandidate
from safefix.testprep.workspace import CandidateWorkspace


def result(
    *failure_ids: str,
    valid: bool = True,
    messages: dict[str, str] | None = None,
) -> _TestRunResult:
    cases = tuple(
        _TestCaseResult(
            failure_id=failure_id,
            classname=failure_id.split("::", 1)[0],
            name=failure_id.split("::", 1)[-1],
            status="failed",
            message=(messages or {}).get(failure_id, ""),
        )
        for failure_id in failure_ids
    )
    return _TestRunResult(
        exit_code=1 if failure_ids else 0,
        cases=cases,
        valid=valid,
    )


def candidate() -> GeneratedTestCandidate:
    return GeneratedTestCandidate(
        candidate_id="c1",
        test_source="def test_generated_value():\n    assert True\n",
        basis="The public contract requires this behavior.",
        sources=("src/app.py",),
    )


class ScriptedRunner:
    def __init__(self, results: list[_TestRunResult]):
        self.results = results
        self.paths: list[Path] = []

    def __call__(self, path: Path) -> _TestRunResult:
        self.paths.append(path)
        return self.results[len(self.paths) - 1]


def test_stable_pass_runs_exactly_configured_number_of_times(tmp_path: Path):
    workspace = CandidateWorkspace(tmp_path, "stability-pass")
    path = workspace.stage(candidate())
    runner = ScriptedRunner([result(), result(), result()])

    evaluation = StabilityRunner(
        runner, stability_runs=3, candidate_root=workspace.session_root
    ).evaluate(path)

    assert evaluation.status is CandidateStatus.PASS
    assert len(evaluation.runs) == 3
    assert len(runner.paths) == 3
    assert all(run_path != path for run_path in runner.paths)
    workspace.cleanup()
    assert evaluation.stable_failure_ids == frozenset()


def test_stable_fail_preserves_failure_ids(tmp_path: Path):
    workspace = CandidateWorkspace(tmp_path, "stability-fail")
    path = workspace.stage(candidate())
    runner = ScriptedRunner(
        [
            result("candidate::test_x"),
            result("candidate::test_x"),
            result("candidate::test_x"),
        ]
    )

    evaluation = StabilityRunner(
        runner, stability_runs=3, candidate_root=workspace.session_root
    ).evaluate(path)

    assert evaluation.status is CandidateStatus.FAIL
    assert evaluation.stable_failure_ids == frozenset({"candidate::test_x"})
    assert all(isinstance(run, CandidateRun) for run in evaluation.runs)
    workspace.cleanup()


def test_invalid_collection_or_infrastructure_run_is_error_not_flaky(tmp_path: Path):
    workspace = CandidateWorkspace(tmp_path, "stability-error")
    path = workspace.stage(candidate())
    runner = ScriptedRunner([result(), result("collection_error::c" , valid=False), result()])

    evaluation = StabilityRunner(
        runner, stability_runs=3, candidate_root=workspace.session_root
    ).evaluate(path)

    assert evaluation.status is CandidateStatus.ERROR
    workspace.cleanup()


def test_mismatched_failure_ids_are_flaky(tmp_path: Path):
    workspace = CandidateWorkspace(tmp_path, "stability-flaky")
    path = workspace.stage(candidate())
    runner = ScriptedRunner(
        [
            result("candidate::test_x"),
            result("candidate::test_y"),
            result("candidate::test_x"),
        ]
    )

    evaluation = StabilityRunner(
        runner, stability_runs=3, candidate_root=workspace.session_root
    ).evaluate(path)

    assert evaluation.status is CandidateStatus.FLAKY
    assert evaluation.stable_failure_ids == frozenset()
    workspace.cleanup()


def test_same_failure_ids_with_different_messages_are_flaky(tmp_path: Path):
    workspace = CandidateWorkspace(tmp_path, "stability-signature-flaky")
    path = workspace.stage(candidate())
    failure_id = "candidate::test_x"
    runner = ScriptedRunner(
        [
            result(failure_id, messages={failure_id: "assert 1 == 2"}),
            result(failure_id, messages={failure_id: "assert 3 == 4"}),
            result(failure_id, messages={failure_id: "assert 5 == 6"}),
        ]
    )

    evaluation = StabilityRunner(
        runner, stability_runs=3, candidate_root=workspace.session_root
    ).evaluate(path)

    assert evaluation.status is CandidateStatus.FLAKY
    assert evaluation.stable_failure_ids == frozenset()
    workspace.cleanup()


def test_valid_runs_that_disagree_between_pass_and_fail_are_flaky(tmp_path: Path):
    workspace = CandidateWorkspace(tmp_path, "stability-disagree")
    path = workspace.stage(candidate())
    runner = ScriptedRunner([result(), result("candidate::test_x"), result()])

    evaluation = StabilityRunner(
        runner, stability_runs=3, candidate_root=workspace.session_root
    ).evaluate(path)

    assert evaluation.status is CandidateStatus.FLAKY
    workspace.cleanup()


def test_stability_run_count_must_be_positive_and_integer(tmp_path: Path):
    workspace = CandidateWorkspace(tmp_path, "stability-count")
    with pytest.raises(ValueError, match="stability_runs"):
        StabilityRunner(lambda path: result(), stability_runs=0, candidate_root=workspace.session_root)
    with pytest.raises(ValueError, match="stability_runs"):
        StabilityRunner(lambda path: result(), stability_runs=True, candidate_root=workspace.session_root)
    with pytest.raises(ValueError, match="stability_runs"):
        StabilityRunner(
            lambda path: result(),
            stability_runs=MAX_STABILITY_RUNS + 1,
            candidate_root=workspace.session_root,
        )
    workspace.cleanup()


def test_stability_runs_restore_original_candidate_for_each_mutating_run(tmp_path: Path):
    workspace = CandidateWorkspace(tmp_path, "stability-mutation")
    path = workspace.stage(candidate())
    observed: list[str] = []

    def mutating_runner(run_path: Path) -> _TestRunResult:
        observed.append(run_path.read_text(encoding="utf-8"))
        run_path.write_text("mutated", encoding="utf-8")
        return result()

    evaluation = StabilityRunner(
        mutating_runner, stability_runs=3, candidate_root=workspace.session_root
    ).evaluate(path)

    assert evaluation.status is CandidateStatus.PASS
    assert observed == [candidate().test_source] * 3
    assert path.read_text(encoding="utf-8") == candidate().test_source
    workspace.cleanup()


@pytest.mark.parametrize(
    "candidate_path",
    [
        "outside.py",
        "staged/../../../outside.py",
    ],
)
def test_stability_rejects_candidate_paths_outside_session_root(
    tmp_path: Path, candidate_path: str
):
    workspace = CandidateWorkspace(tmp_path, "stability-boundary")
    outside = tmp_path / "outside.py"
    outside.write_text(candidate().test_source, encoding="utf-8")
    runner = ScriptedRunner([result()])

    with pytest.raises(ValueError, match="candidate"):
        StabilityRunner(
            runner, stability_runs=1, candidate_root=workspace.session_root
        ).evaluate(outside if candidate_path == "outside.py" else workspace.session_root / candidate_path)

    assert runner.paths == []
    workspace.cleanup()


def test_stability_rejects_forged_marker_but_accepts_workspace_root(
    tmp_path: Path,
):
    project_root = tmp_path / "project"
    project_root.mkdir()
    workspace = CandidateWorkspace(project_root, "genuine-session")
    genuine_path = workspace.stage(candidate())
    genuine_runner = ScriptedRunner([result()])

    evaluation = StabilityRunner(
        genuine_runner, stability_runs=1, candidate_root=workspace.session_root
    ).evaluate(genuine_path)

    assert evaluation.status is CandidateStatus.PASS

    forged_root = tmp_path / "outside" / "session"
    forged_root.mkdir(parents=True)
    (forged_root / ".session-owner").write_text("forged", encoding="utf-8")
    forged_candidate = forged_root / "candidate.py"
    forged_candidate.write_text(candidate().test_source, encoding="utf-8")
    forged_runner = ScriptedRunner([result()])

    with pytest.raises(ValueError, match="CandidateWorkspace"):
        StabilityRunner(
            forged_runner, stability_runs=1, candidate_root=forged_root
        ).evaluate(forged_candidate)

    assert forged_runner.paths == []
    workspace.cleanup()


def test_stability_revalidates_marker_before_evaluation(
    tmp_path: Path,
):
    workspace = CandidateWorkspace(tmp_path, "marker-lifecycle")
    path = workspace.stage(candidate())
    calls: list[Path] = []

    def callback(run_path: Path) -> _TestRunResult:
        calls.append(run_path)
        return result()

    stability = StabilityRunner(
        callback, stability_runs=1, candidate_root=workspace.session_root
    )
    (workspace.session_root / ".session-owner").unlink()

    with pytest.raises(ValueError, match="CandidateWorkspace"):
        stability.evaluate(path)

    assert calls == []


def test_stability_rejects_session_root_replacement_after_construction(
    tmp_path: Path,
):
    workspace = CandidateWorkspace(tmp_path, "replacement-lifecycle")

    calls: list[Path] = []

    def callback(run_path: Path) -> _TestRunResult:
        calls.append(run_path)
        return result()

    stability = StabilityRunner(
        callback,
        stability_runs=1,
        candidate_root=workspace.session_root,
    )
    workspace.cleanup()

    replacement = CandidateWorkspace(tmp_path, "replacement-lifecycle")
    replacement_path = replacement.stage(candidate())

    with pytest.raises(ValueError, match="CandidateWorkspace"):
        stability.evaluate(replacement_path)

    assert calls == []
    replacement.cleanup()


def test_config_accepts_maximum_bounded_stability_runs(tmp_path: Path):
    config = load_config(tmp_path, {"stability_runs": MAX_STABILITY_RUNS})

    assert config.stability_runs == MAX_STABILITY_RUNS


def test_config_rejects_stability_runs_above_bound(tmp_path: Path):
    with pytest.raises(ConfigError, match="stability_runs"):
        load_config(tmp_path, {"stability_runs": MAX_STABILITY_RUNS + 1})
