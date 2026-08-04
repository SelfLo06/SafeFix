from pathlib import Path
import shutil

import pytest

from safefix.llm.mock import MockLLM
from safefix.models import GuardDecision, StopReason
from safefix.runner import SessionRunner


class FakeCredentials:
    def get(self) -> str:
        return "offline-test-key"


class RecordingApproval:
    def __init__(self) -> None:
        self.calls = 0

    def approve(self, action: object) -> bool:
        self.calls += 1
        return True


@pytest.fixture
def project(tmp_path: Path) -> Path:
    fixture = Path(__file__).parents[1] / "fixtures" / "projects" / "single_failure"
    shutil.copytree(fixture, tmp_path, dirs_exist_ok=True)
    return tmp_path


def test_demo_test_edit_is_permanently_denied(project: Path) -> None:
    approval = RecordingApproval()
    original = (project / "tests" / "app_tests.py").read_text(encoding="utf-8")
    runner = SessionRunner(
        project,
        credentials=FakeCredentials(),
        llm_client=MockLLM(
            [
                '{"tool": "apply_patch", "changes": [{"path": "tests/app_tests.py", "old_text": "assert value == 2", "new_text": "assert value == 1"}]}',
                '{"tool": "finish", "reason": "test edit denied"}',
            ]
        ),
        approval=approval,
    )

    result = runner.run()

    assert result.stop_reason is StopReason.REQUESTED
    assert (project / "tests" / "app_tests.py").read_text(encoding="utf-8") == original
    assert runner.state is not None
    assert runner.state.recent_guard_events[0][1] is GuardDecision.DENY
    assert runner.state.recent_tool_events[0][1].outcome == "denied"
    assert approval.calls == 0
