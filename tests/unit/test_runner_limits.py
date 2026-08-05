from pathlib import Path

import pytest

from safefix.junit import TestCaseResult as _TestCaseResult
from safefix.llm.base import LLMResponseError, LLMTransportError
from safefix.llm.mock import MockLLM
from safefix.models import StopReason
from safefix.memory import ProjectMemoryStore
from safefix.testrunner import TestRunResult as _TestRunResult


class FakeCredentials:
    def get(self) -> str:
        return "test-api-key"


class SequentialTestRunner:
    def __init__(self, reports: list[_TestRunResult]) -> None:
        self._reports = iter(reports)

    def run(self) -> _TestRunResult:
        return next(self._reports)


class FailingTransportLLM:
    def __init__(self) -> None:
        self.attempts = 0

    def complete(self, prompt: str) -> str:
        self.attempts += 1
        raise LLMTransportError("transport unavailable")


class FailingResponseLLM:
    def complete(self, prompt: str) -> str:
        raise LLMResponseError("invalid response")


class MutatingFinishLLM:
    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root
        self._responses = iter(
            [
                '{"tool": "apply_patch", "changes": ['
                '{"path": "src/app.py", "old_text": "value = 1", "new_text": "value = 2"}, '
                '{"path": "src/helper.py", "old_text": "helper = 1", "new_text": "helper = 2"}'
                ']}'
            ]
        )

    def complete(self, prompt: str) -> str:
        try:
            return next(self._responses)
        except StopIteration:
            (self._project_root / "src" / "app.py").write_text("value = 99\n", encoding="utf-8")
            (self._project_root / "src" / "helper.py").write_text("helper = 99\n", encoding="utf-8")
            return '{"tool": "finish", "reason": "done"}'


class CapturingFinishLLM:
    def __init__(self) -> None:
        self.prompt = ""

    def complete(self, prompt: str) -> str:
        self.prompt = prompt
        return '{"tool": "finish", "reason": "done"}'


def _report(exit_code: int, *failure_ids: str) -> _TestRunResult:
    return _TestRunResult(
        exit_code=exit_code,
        cases=tuple(
            _TestCaseResult(failure_id, *failure_id.rsplit("::", 1), "failed")
            for failure_id in failure_ids
        ),
        valid=True,
    )


def _project(tmp_path: Path, *, helper: bool = False) -> None:
    (tmp_path / "safefix.toml").write_text(
        'base_url = "https://example.invalid"\nmodel = "test-model"\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    if helper:
        (tmp_path / "src" / "helper.py").write_text("helper = 1\n", encoding="utf-8")


def _runner(
    tmp_path: Path,
    reports: list[_TestRunResult],
    llm_client: object,
    **overrides: object,
):
    from safefix.runner import SessionRunner

    test_runner = SequentialTestRunner(reports)
    runner_options = {
        key: overrides.pop(key)
        for key in ("use_memory", "memory_store")
        if key in overrides
    }
    return SessionRunner(
        tmp_path,
        cli_overrides=overrides,
        credentials=FakeCredentials(),
        llm_client=llm_client,
        test_runner_factory=lambda project_root, pytest_args: test_runner,
        **runner_options,
    )


def test_parse_error_consumes_step_not_round(tmp_path: Path) -> None:
    _project(tmp_path)
    runner = _runner(
        tmp_path,
        [_report(1, "tests.app::test_value")],
        MockLLM(["not JSON", '{"tool": "finish", "reason": "done"}']),
    )

    result = runner.run()

    assert result.stop_reason is StopReason.REQUESTED
    assert (result.steps, result.rounds) == (2, 0)
    assert runner.state is not None
    assert runner.state.recent_tool_events[0][1].outcome == "parse_error"


def test_read_tool_error_becomes_feedback_and_loop_continues(tmp_path: Path) -> None:
    _project(tmp_path)
    runner = _runner(
        tmp_path,
        [_report(1, "tests.app::test_value")],
        MockLLM([
            '{"tool": "read_file", "path": "missing.py"}',
            '{"tool": "finish"}',
        ]),
    )

    result = runner.run()

    assert result.stop_reason is StopReason.REQUESTED
    assert runner.state is not None
    assert runner.state.recent_tool_events[0][1].outcome == "error"


def test_max_steps_stop(tmp_path: Path) -> None:
    _project(tmp_path)
    runner = _runner(
        tmp_path,
        [_report(1, "tests.app::test_value")],
        MockLLM(['{"tool": "read_file", "path": "src/app.py"}']),
        max_steps=1,
    )

    result = runner.run()

    assert result.stop_reason is StopReason.MAX_STEPS
    assert (result.steps, result.rounds) == (1, 0)


def test_max_rounds_stop(tmp_path: Path) -> None:
    _project(tmp_path)
    runner = _runner(
        tmp_path,
        [_report(1, "tests.app::test_a", "tests.app::test_b"), _report(1, "tests.app::test_a")],
        MockLLM([
            '{"tool": "apply_patch", "changes": [{"path": "src/app.py", "old_text": "value = 1", "new_text": "value = 2"}]}'
        ]),
        max_rounds=1,
    )

    result = runner.run()

    assert result.stop_reason is StopReason.MAX_ROUNDS
    assert (result.steps, result.rounds) == (1, 1)


def test_no_progress_stop(tmp_path: Path) -> None:
    _project(tmp_path)
    runner = _runner(
        tmp_path,
        [_report(1, "tests.app::test_value"), _report(1, "tests.app::test_value")],
        MockLLM([
            '{"tool": "apply_patch", "changes": [{"path": "src/app.py", "old_text": "value = 1", "new_text": "value = 2"}]}'
        ]),
        max_no_progress_rounds=1,
    )

    result = runner.run()

    assert result.stop_reason is StopReason.NO_PROGRESS
    assert (result.steps, result.rounds, result.no_progress) == (1, 1, 1)


def test_transport_retry_then_error(tmp_path: Path) -> None:
    _project(tmp_path)
    llm = FailingTransportLLM()
    runner = _runner(tmp_path, [_report(1, "tests.app::test_value")], llm)

    result = runner.run()

    assert result.stop_reason is StopReason.ERROR
    assert result.steps == 1
    assert llm.attempts == 3


def test_response_error_stops_runtime_error(tmp_path: Path) -> None:
    _project(tmp_path)
    runner = _runner(tmp_path, [_report(1, "tests.app::test_value")], FailingResponseLLM())

    result = runner.run()

    assert result.stop_reason is StopReason.ERROR


def test_exhausted_mock_script_stops_with_runtime_error(tmp_path: Path) -> None:
    _project(tmp_path)
    runner = _runner(tmp_path, [_report(1, "tests.app::test_value")], MockLLM([]))

    result = runner.run()

    assert result.stop_reason is StopReason.ERROR
    assert result.exit_code == 3


def test_artifact_write_failure_maps_to_runtime_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path)
    runner = _runner(
        tmp_path,
        [_report(1, "tests.app::test_value")],
        MockLLM(['{"tool": "read_file", "path": "src/app.py"}']),
        max_steps=1,
    )

    class FailingArtifactWriter:
        def __init__(self, path: Path) -> None:
            pass

        def write(self, state: object, result: object) -> object:
            raise OSError("artifact unavailable")

    monkeypatch.setattr("safefix.runner.ArtifactWriter", FailingArtifactWriter)

    result = runner.run()

    assert result.stop_reason is StopReason.ERROR


def test_stop_restores_all_touched_files_and_writes_artifact(tmp_path: Path) -> None:
    _project(tmp_path, helper=True)
    runner = _runner(
        tmp_path,
        [_report(1, "tests.app::test_a", "tests.app::test_b"), _report(1, "tests.app::test_a")],
        MutatingFinishLLM(tmp_path),
    )

    result = runner.run()

    assert result.stop_reason is StopReason.REQUESTED
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "value = 2\n"
    assert (tmp_path / "src" / "helper.py").read_text(encoding="utf-8") == "helper = 2\n"
    assert result.artifact_path is not None
    assert Path(result.artifact_path).is_file()


def test_memory_persistence_failure_is_runtime_error(tmp_path: Path) -> None:
    _project(tmp_path)

    class FailingMemoryStore:
        def update(self, summary: str, *, unsuccessful_patch_fingerprints=()):
            raise OSError("memory unavailable")

        def load(self, *, use_memory: bool = False):
            return ()

        def load_fingerprints(self, *, use_memory: bool = False):
            return ()

    runner = _runner(
        tmp_path,
        [_report(1, "tests.app::test_value")],
        CapturingFinishLLM(),
        use_memory=True,
        memory_store=FailingMemoryStore(),
    )

    result = runner.run()

    assert result.stop_reason is StopReason.ERROR
