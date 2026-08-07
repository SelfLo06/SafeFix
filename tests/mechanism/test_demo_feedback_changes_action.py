from pathlib import Path
import shutil

import pytest

from safefix.llm.mock import MockLLM
from safefix.models import StopReason, ToolName
from safefix.runner import SessionRunner


class FakeCredentials:
    def get(self) -> str:
        return "offline-test-key"


class FakeApproval:
    def approve(self, action: object) -> bool:
        return True


@pytest.fixture
def project(tmp_path: Path) -> Path:
    fixture = Path(__file__).parents[1] / "fixtures" / "projects" / "single_failure"
    shutil.copytree(fixture, tmp_path, dirs_exist_ok=True)
    return tmp_path


def test_feedback_changes_the_next_scripted_action(project: Path) -> None:
    runner = SessionRunner(
        project,
        credentials=FakeCredentials(),
        llm_client=MockLLM(
            [
                '{"tool": "apply_patch", "changes": [{"path": "tests/app_tests.py", "old_text": "assert value == 2", "new_text": "assert value == 1"}]}',
                '{"tool": "apply_patch", "changes": [{"path": "src/app.py", "old_text": "value = 1", "new_text": "value = 2"}]}',
            ]
        ),
        approval=FakeApproval(),
    )

    result = runner.run()

    assert result.stop_reason is StopReason.SUCCESS
    assert runner.state is not None
    assert [event[1].outcome for event in runner.state.recent_tool_events] == [
        "denied",
        "success",
    ]
    assert [event[0].tool for event in runner.state.recent_tool_events] == [
        ToolName.APPLY_PATCH,
        ToolName.APPLY_PATCH,
    ]
    assert runner.state.F0.ids == frozenset({"tests.app_tests::test_value"})
    assert (project / "src" / "app.py").read_text(encoding="utf-8") == "value = 2\n"
