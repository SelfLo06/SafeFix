from pathlib import Path

import pytest

from safefix.junit import TestCaseResult as _TestCaseResult
from safefix.llm.mock import MockLLM
from safefix.models import StopReason
from safefix.testrunner import TestRunResult as _TestRunResult


class FakeCredentials:
    def get(self) -> str:
        return "test-api-key"


class FakeTestRunner:
    def __init__(self, result: _TestRunResult) -> None:
        self._result = result

    def run(self) -> _TestRunResult:
        return self._result


def _project(tmp_path: Path) -> None:
    (tmp_path / "safefix.toml").write_text(
        'base_url = "https://example.invalid"\nmodel = "test-model"\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_value():\n    pass\n", encoding="utf-8")


def _runner(tmp_path: Path, responses: list[str]):
    from safefix.runner import SessionRunner

    baseline = _TestRunResult(
        exit_code=1,
        cases=(_TestCaseResult("tests.app::test_value", "tests.app", "test_value", "failed"),),
    )
    return SessionRunner(
        tmp_path,
        credentials=FakeCredentials(),
        llm_client=MockLLM(responses),
        test_runner_factory=lambda project_root, pytest_args: FakeTestRunner(baseline),
    )


def test_read_tool_returns_to_ready(tmp_path: Path) -> None:
    _project(tmp_path)
    runner = _runner(
        tmp_path,
        [
            '{"tool": "read_file", "path": "src/app.py"}',
            '{"tool": "finish", "reason": "inspected source"}',
        ],
    )

    result = runner.run()

    assert result.stop_reason is StopReason.REQUESTED
    assert result.steps == 2
    assert runner.state is not None
    assert [feedback.outcome for _, feedback in runner.state.recent_tool_events] == [
        "completed"
    ]


@pytest.mark.parametrize(
    "action",
    [
        '{"tool": "list_dir", "path": "src"}',
        '{"tool": "search_code", "path": "src", "query": "value"}',
    ],
)
def test_list_and_search_tools_return_to_ready(tmp_path: Path, action: str) -> None:
    _project(tmp_path)
    runner = _runner(
        tmp_path,
        [action, '{"tool": "finish", "reason": "inspected source"}'],
    )

    result = runner.run()

    assert result.stop_reason is StopReason.REQUESTED
    assert result.steps == 2
    assert runner.state is not None
    assert runner.state.recent_tool_events[0][1].outcome == "completed"


def test_finish_stops_requested(tmp_path: Path) -> None:
    _project(tmp_path)
    runner = _runner(tmp_path, ['{"tool": "finish", "reason": "request stop"}'])

    result = runner.run()

    assert result.stop_reason is StopReason.REQUESTED
    assert result.steps == 1
    assert result.rounds == 0


def test_denied_patch_returns_feedback_without_round(tmp_path: Path) -> None:
    _project(tmp_path)
    runner = _runner(
        tmp_path,
        [
            '{"tool": "apply_patch", "changes": [{"path": "tests/test_app.py", "old_text": "pass", "new_text": "assert False"}]}',
            '{"tool": "finish", "reason": "patch was denied"}',
        ],
    )

    result = runner.run()

    assert result.stop_reason is StopReason.REQUESTED
    assert result.steps == 2
    assert result.rounds == 0
    assert runner.state is not None
    assert runner.state.recent_guard_events[0][1].value == "deny"
    assert runner.state.recent_tool_events[-1][1].outcome == "denied"
