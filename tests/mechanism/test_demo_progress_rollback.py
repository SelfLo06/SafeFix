from pathlib import Path
import shutil

import pytest

from safefix.llm.mock import MockLLM
from safefix.models import StopReason
from safefix.runner import SessionRunner


class FakeCredentials:
    def get(self) -> str:
        return "offline-test-key"


class FakeApproval:
    def approve(self, action: object) -> bool:
        return True


@pytest.fixture
def project(tmp_path: Path) -> Path:
    fixture = Path(__file__).parents[1] / "fixtures" / "projects" / "progress"
    shutil.copytree(fixture, tmp_path, dirs_exist_ok=True)
    return tmp_path


def test_better_same_worse_and_no_progress_are_deterministic(project: Path) -> None:
    runner = SessionRunner(
        project,
        cli_overrides={"max_no_progress_rounds": 3},
        credentials=FakeCredentials(),
        llm_client=MockLLM(
            [
                '{"tool": "apply_patch", "changes": [{"path": "src/app.py", "old_text": "value = 1", "new_text": "value = 2"}]}',
                '{"tool": "apply_patch", "changes": [{"path": "src/app.py", "old_text": "regression = 1", "new_text": "regression = 1 # same"}]}',
                '{"tool": "apply_patch", "changes": [{"path": "src/app.py", "old_text": "stable = 1", "new_text": "stable = 0"}]}',
                '{"tool": "apply_patch", "changes": [{"path": "src/app.py", "old_text": "regression = 1", "new_text": "regression = 1 # same again"}]}',
            ]
        ),
        approval=FakeApproval(),
    )
    result = runner.run()

    assert result.stop_reason is StopReason.NO_PROGRESS
    assert (result.rounds, result.no_progress) == (4, 3)
    assert runner.state is not None
    assert [feedback.outcome for _, feedback in runner.state.recent_tool_events] == [
        "better",
        "same",
        "worse",
        "same",
    ]
    assert (project / "src" / "app.py").read_text(encoding="utf-8") == (
        "value = 2\nregression = 1\nstable = 1\n"
    )
