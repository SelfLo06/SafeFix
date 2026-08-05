from pathlib import Path

import pytest

from safefix.junit import TestCaseResult as _TestCaseResult
from safefix.models import FailureSet, StopReason
from safefix.testrunner import TestRunResult as _TestRunResult


class FakeCredentials:
    def get(self) -> str:
        return "test-api-key"


class FakeTestRunner:
    def __init__(self, result: _TestRunResult) -> None:
        self._result = result

    def run(self) -> _TestRunResult:
        return self._result


def _project(tmp_path: Path, *, source: bool = True) -> None:
    (tmp_path / "safefix.toml").write_text(
        'base_url = "https://example.invalid"\nmodel = "test-model"\n',
        encoding="utf-8",
    )
    if source:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")


def _runner(tmp_path: Path, result: _TestRunResult, **overrides: object):
    from safefix.runner import SessionRunner

    return SessionRunner(
        tmp_path,
        cli_overrides=overrides,
        credentials=FakeCredentials(),
        test_runner_factory=lambda project_root, pytest_args: FakeTestRunner(result),
    )


def test_valid_baseline_freezes_f0(tmp_path: Path) -> None:
    _project(tmp_path)
    baseline = _TestRunResult(
        exit_code=1,
        cases=(_TestCaseResult("tests.app::test_value", "tests.app", "test_value", "failed"),),
        valid=True,
    )

    runner = _runner(tmp_path, baseline)

    assert runner.initialize() is None
    assert runner.state is not None
    assert runner.state.F0.ids == frozenset({"tests.app::test_value"})
    assert runner.state.F == runner.state.U_best == runner.state.F0
    assert runner.snapshot_store is not None
    assert runner.snapshot_store.best_contents == {}
    with pytest.raises(AttributeError):
        runner.state.F0 = FailureSet(frozenset())


def test_valid_red_report_with_nonstandard_pytest_exit_is_accepted(tmp_path: Path) -> None:
    _project(tmp_path)
    baseline = _TestRunResult(
        exit_code=2,
        cases=(_TestCaseResult("tests.app::test_value", "tests.app", "test_value", "failed"),),
        valid=True,
    )

    assert _runner(tmp_path, baseline).initialize() is None


def test_valid_empty_baseline_stops_success(tmp_path: Path) -> None:
    _project(tmp_path, source=False)
    runner = _runner(
        tmp_path,
        _TestRunResult(
            exit_code=0,
            cases=(_TestCaseResult("tests.app::test_passed", "tests.app", "test_passed", "passed"),),
            valid=True,
        ),
    )

    result = runner.initialize()

    assert result is not None
    assert result.stop_reason is StopReason.SUCCESS
    assert runner.state is not None
    assert runner.state.F0.ids == frozenset()


def test_invalid_baseline_stops_config_error(tmp_path: Path) -> None:
    _project(tmp_path)
    runner = _runner(tmp_path, _TestRunResult(exit_code=2, cases=()))

    result = runner.initialize()

    assert result is not None
    assert result.stop_reason is StopReason.CONFIG_ERROR
    assert runner.state is None


def test_baseline_without_collected_tests_stops_config_error(tmp_path: Path) -> None:
    _project(tmp_path)
    runner = _runner(tmp_path, _TestRunResult(exit_code=0, cases=(), valid=False))

    result = runner.initialize()

    assert result is not None
    assert result.stop_reason is StopReason.CONFIG_ERROR


def test_baseline_process_failure_stops_runtime_error(tmp_path: Path) -> None:
    _project(tmp_path)
    runner = _runner(tmp_path, _TestRunResult(exit_code=3, cases=(), valid=False))

    result = runner.initialize()

    assert result is not None
    assert result.stop_reason is StopReason.ERROR


def test_invalid_config_path_stops_config_error(tmp_path: Path) -> None:
    _project(tmp_path)
    runner = _runner(
        tmp_path,
        _TestRunResult(exit_code=0, cases=()),
        allowed_paths=["../outside"],
    )

    result = runner.initialize()

    assert result is not None
    assert result.stop_reason is StopReason.CONFIG_ERROR


def test_baseline_runner_start_failure_stops_runtime_error(tmp_path: Path) -> None:
    _project(tmp_path)
    from safefix.runner import SessionRunner

    def failing_factory(project_root: Path, pytest_args: list[str]) -> _TestRunResult:
        raise OSError("pytest could not start")

    runner = SessionRunner(
        tmp_path,
        credentials=FakeCredentials(),
        test_runner_factory=failing_factory,
    )

    result = runner.initialize()

    assert result is not None
    assert result.stop_reason is StopReason.ERROR


@pytest.mark.parametrize("project_kind", ["missing", "file"])
def test_invalid_project_path_stops_config_error_before_baseline(
    tmp_path: Path, project_kind: str
) -> None:
    invalid_project = tmp_path / "invalid-project"
    if project_kind == "file":
        invalid_project.write_text("not a project directory\n", encoding="utf-8")
    called = False

    def baseline_factory(project_root: Path, pytest_args: list[str]) -> FakeTestRunner:
        nonlocal called
        called = True
        return FakeTestRunner(_TestRunResult(exit_code=3, cases=(), valid=False))

    from safefix.models import Config
    from safefix.runner import SessionRunner

    runner = SessionRunner(
        invalid_project,
        credentials=FakeCredentials(),
        config_loader=lambda root, overrides, require_llm: Config(
            base_url="https://example.invalid", model="test-model"
        ),
        test_runner_factory=baseline_factory,
    )

    result = runner.initialize()

    assert result is not None
    assert result.stop_reason is StopReason.CONFIG_ERROR
    assert runner.state is None
    assert called is False


@pytest.mark.parametrize("allowed_path", ["env.lock", "tests"])
def test_explicit_hard_denied_allowed_path_stops_config_error(
    tmp_path: Path, allowed_path: str
) -> None:
    _project(tmp_path)
    runner = _runner(
        tmp_path,
        _TestRunResult(exit_code=1, cases=(), valid=False),
        allowed_paths=[allowed_path],
    )

    result = runner.initialize()

    assert result is not None
    assert result.stop_reason is StopReason.CONFIG_ERROR
    assert runner.state is None


def test_preinit_config_error_does_not_write_session_artifact(tmp_path: Path) -> None:
    runner = _runner(
        tmp_path,
        _TestRunResult(exit_code=1, cases=(), valid=False),
    )

    result = runner.run()

    assert result.stop_reason is StopReason.CONFIG_ERROR
    assert not (tmp_path / "safefix-session.json").exists()


def test_nonempty_f0_with_empty_writable_set_stops_config_error(tmp_path: Path) -> None:
    _project(tmp_path, source=False)
    baseline = _TestRunResult(
        exit_code=1,
        cases=(_TestCaseResult("tests.app::test_value", "tests.app", "test_value", "failed"),),
        valid=True,
    )
    runner = _runner(tmp_path, baseline)

    result = runner.initialize()

    assert result is not None
    assert result.stop_reason is StopReason.CONFIG_ERROR
    assert runner.state is not None
    assert runner.state.F0.ids == frozenset({"tests.app::test_value"})
