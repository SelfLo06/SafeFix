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


def test_runner_accepts_v02_setup_factory_and_freezes_formal_f0(tmp_path: Path) -> None:
    from safefix.models import BaselineSource, Config
    from safefix.session_setup import manifest_from_entries
    from safefix.test_manifest import ManifestEntry
    from safefix.testprep.service import PreparationResult, PreparationSummary
    from safefix.runner import SessionRunner

    _project(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_existing.py").write_text(
        "def test_existing():\n    assert False\n", encoding="utf-8"
    )
    baseline = _TestRunResult(
        exit_code=1,
        cases=(_TestCaseResult("tests.test_existing::test_existing", "tests.test_existing", "test_existing", "failed"),),
        valid=True,
    )
    formal_calls = 0

    class Runner:
        def __init__(self, target_paths: tuple[str, ...], allow_empty: bool) -> None:
            self.target_paths = target_paths
            self.allow_empty = allow_empty

        def run(self):
            nonlocal formal_calls
            formal_calls += 1
            return baseline

        def collect_test_paths(self):
            return ("tests/test_existing.py",)

    def preparation_factory(request):
        return PreparationResult(
            (ManifestEntry("tests/test_existing.py", "0" * 64, BaselineSource.EXISTING),),
            PreparationSummary(BaselineSource.EXISTING, existing_test_count=1),
        )

    runner = SessionRunner(
        tmp_path,
        credentials=FakeCredentials(),
        test_runner_factory=lambda _root, _args, *, target_paths, allow_empty: Runner(
            tuple(target_paths), allow_empty
        ),
        config_loader=lambda *_args, **_kwargs: Config(
            base_url="https://example.invalid", model="test-model"
        ),
        preparation_factory=preparation_factory,
        manifest_factory=manifest_from_entries,
    )

    assert runner.initialize() is None
    assert formal_calls == 2
    assert runner.state is not None
    assert runner.state.F0.ids == frozenset({"tests.test_existing::test_existing"})
    assert runner.state.repair_required is True


def test_v02_runner_factory_uses_frozen_targets_for_formal_and_evaluation(
    tmp_path: Path,
) -> None:
    from safefix.llm.mock import MockLLM
    from safefix.models import BaselineSource, Config
    from safefix.session_setup import manifest_from_entries
    from safefix.test_manifest import ManifestEntry
    from safefix.testprep.service import PreparationResult, PreparationSummary
    from safefix.runner import SessionRunner

    _project(tmp_path)
    (tmp_path / "tests").mkdir()
    test_path = "tests/test_existing.py"
    (tmp_path / test_path).write_text(
        "def test_existing():\n    assert False\n", encoding="utf-8"
    )
    baseline = _TestRunResult(
        exit_code=1,
        cases=(
            _TestCaseResult(
                "tests.test_existing::test_existing",
                "tests.test_existing",
                "test_existing",
                "failed",
            ),
        ),
        valid=True,
    )
    evaluation = _TestRunResult(
        exit_code=1,
        cases=(
            _TestCaseResult(
                "tests.test_existing::test_existing",
                "tests.test_existing",
                "test_existing",
                "failed",
            ),
        ),
        valid=True,
    )
    supplied: list[tuple[str, ...]] = []
    used: list[tuple[str, ...]] = []

    class CapturingRunner:
        def __init__(self, target_paths: tuple[str, ...], result: _TestRunResult):
            self.target_paths = target_paths
            self.allow_empty = False
            self._result = result

        def run(self) -> _TestRunResult:
            used.append(self.target_paths)
            return self._result

        def collect_test_paths(self) -> tuple[str, ...]:
            return (test_path,)

    def runner_factory(
        project_root: Path,
        pytest_args: list[str],
        *,
        target_paths: tuple[str, ...],
        allow_empty: bool,
    ) -> CapturingRunner:
        del project_root, pytest_args
        supplied.append(tuple(target_paths))
        if not target_paths:
            runner = CapturingRunner((), _TestRunResult(0, (), valid=True))
            runner.allow_empty = allow_empty
            return runner
        result = baseline if supplied.count((test_path,)) == 1 else evaluation
        runner = CapturingRunner(tuple(target_paths), result)
        runner.allow_empty = allow_empty
        return runner

    def preparation_factory(request):
        return PreparationResult(
            (ManifestEntry(test_path, "0" * 64, BaselineSource.EXISTING),),
            PreparationSummary(BaselineSource.EXISTING, existing_test_count=1),
        )

    runner = SessionRunner(
        tmp_path,
        credentials=FakeCredentials(),
        llm_client=MockLLM(
            [
                '{"tool": "apply_patch", "changes": [{"path": "src/app.py", "old_text": "value = 1", "new_text": "value = 2"}]}',
                '{"tool": "finish", "reason": "done"}',
            ]
        ),
        test_runner_factory=runner_factory,
        config_loader=lambda *_args, **_kwargs: Config(
            base_url="https://example.invalid", model="test-model"
        ),
        preparation_factory=preparation_factory,
        manifest_factory=manifest_from_entries,
    )

    result = runner.run()

    assert result.stop_reason is StopReason.REQUESTED
    assert supplied == [(), (test_path,), (test_path,)]
    assert used == supplied
    assert runner.state is not None
    assert runner.state.F0.ids == frozenset({"tests.test_existing::test_existing"})


def test_v02_runner_factory_cannot_report_a_scope_different_from_manifest(
    tmp_path: Path,
) -> None:
    from safefix.models import BaselineSource, Config
    from safefix.session_setup import manifest_from_entries
    from safefix.test_manifest import ManifestEntry
    from safefix.testprep.service import PreparationResult, PreparationSummary
    from safefix.runner import SessionRunner

    _project(tmp_path)
    (tmp_path / "tests").mkdir()
    test_path = "tests/test_existing.py"
    (tmp_path / test_path).write_text(
        "def test_existing():\n    assert False\n", encoding="utf-8"
    )

    class ScopeIgnoringRunner:
        target_paths = ()
        allow_empty = False

        def run(self) -> _TestRunResult:
            return _TestRunResult(0, (), valid=True)

        def collect_test_paths(self) -> tuple[str, ...]:
            return (test_path,)

    def runner_factory(
        project_root: Path,
        pytest_args: list[str],
        *,
        target_paths: tuple[str, ...],
        allow_empty: bool,
    ) -> ScopeIgnoringRunner:
        del project_root, pytest_args, target_paths, allow_empty
        return ScopeIgnoringRunner()

    def preparation_factory(request):
        return PreparationResult(
            (ManifestEntry(test_path, "0" * 64, BaselineSource.EXISTING),),
            PreparationSummary(BaselineSource.EXISTING, existing_test_count=1),
        )

    runner = SessionRunner(
        tmp_path,
        credentials=FakeCredentials(),
        test_runner_factory=runner_factory,
        config_loader=lambda *_args, **_kwargs: Config(
            base_url="https://example.invalid", model="test-model"
        ),
        preparation_factory=preparation_factory,
        manifest_factory=manifest_from_entries,
    )

    result = runner.initialize()

    assert result is not None
    assert result.stop_reason is StopReason.ERROR
    assert runner.state is None


@pytest.mark.parametrize("mutation", ["change", "remove"])
def test_post_freeze_manifest_mutation_stops_before_evaluation_and_f0_stays_fixed(
    tmp_path: Path, mutation: str
) -> None:
    from safefix.models import BaselineSource, Config
    from safefix.session_setup import manifest_from_entries
    from safefix.test_manifest import ManifestEntry
    from safefix.testprep.service import PreparationResult, PreparationSummary
    from safefix.runner import SessionRunner

    _project(tmp_path)
    (tmp_path / "tests").mkdir()
    test_path = "tests/test_existing.py"
    test_file = tmp_path / test_path
    test_file.write_text("def test_existing():\n    assert False\n", encoding="utf-8")
    baseline = _TestRunResult(
        1,
        (_TestCaseResult("tests.test_existing::test_existing", "tests.test_existing", "test_existing", "failed"),),
        valid=True,
    )
    supplied: list[tuple[str, ...]] = []

    class Runner:
        def __init__(self, target_paths: tuple[str, ...], allow_empty: bool):
            self.target_paths = target_paths
            self.allow_empty = allow_empty

        def run(self) -> _TestRunResult:
            if self.target_paths:
                return baseline
            return _TestRunResult(0, (), valid=True)

        def collect_test_paths(self) -> tuple[str, ...]:
            return (test_path,)

    def runner_factory(
        project_root: Path,
        pytest_args: list[str],
        *,
        target_paths: tuple[str, ...],
        allow_empty: bool,
    ) -> Runner:
        del project_root, pytest_args
        supplied.append(tuple(target_paths))
        return Runner(tuple(target_paths), allow_empty)

    def preparation_factory(request):
        return PreparationResult(
            (ManifestEntry(test_path, "0" * 64, BaselineSource.EXISTING),),
            PreparationSummary(BaselineSource.EXISTING, existing_test_count=1),
        )

    class MutatingLLM:
        def complete(self, prompt: str) -> str:
            del prompt
            if mutation == "remove":
                test_file.unlink()
            else:
                test_file.write_text(
                    "def test_existing():\n    assert True\n", encoding="utf-8"
                )
            return (
                '{"tool": "apply_patch", "changes": [{"path": "src/app.py", '
                '"old_text": "value = 1", "new_text": "value = 2"}]}'
            )

    runner = SessionRunner(
        tmp_path,
        credentials=FakeCredentials(),
        llm_client=MutatingLLM(),
        test_runner_factory=runner_factory,
        config_loader=lambda *_args, **_kwargs: Config(
            base_url="https://example.invalid", model="test-model"
        ),
        preparation_factory=preparation_factory,
        manifest_factory=manifest_from_entries,
    )

    result = runner.run()

    assert result.stop_reason is StopReason.ERROR
    assert supplied == [(), (test_path,)]
    assert runner.state is not None
    assert runner.state.F0.ids == frozenset({"tests.test_existing::test_existing"})
