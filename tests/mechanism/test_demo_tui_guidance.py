from pathlib import Path
import json
import shutil

from safefix.llm.mock import MockLLM
from safefix.models import StopReason
from safefix.operator import OperatorCommandQueue
from safefix.runner import SessionRunner


class _Credentials:
    def get(self) -> str:
        return "offline-test-key"


class _GuidanceLLM:
    def __init__(self, queue: OperatorCommandQueue) -> None:
        self.queue = queue
        self.prompts: list[str] = []
        self.calls = 0

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        self.calls += 1
        if self.calls == 1:
            self.queue.submit_text("preserve the public API")
            return json.dumps({
                "tool": "apply_patch",
                "changes": [{
                    "path": "tests/app_tests.py",
                    "old_text": "assert value == 2",
                    "new_text": "assert value == 1",
                }],
            })
        return json.dumps({
            "tool": "apply_patch",
            "changes": [{
                "path": "src/app.py",
                "old_text": "value = 1",
                "new_text": "value = 2",
            }],
        })

def run_guidance_demo(tmp_path: Path):
    fixture = Path(__file__).parents[1] / "fixtures" / "projects" / "single_failure"
    shutil.copytree(fixture, tmp_path, dirs_exist_ok=True)
    command_queue = OperatorCommandQueue()
    llm = _GuidanceLLM(command_queue)
    runner = SessionRunner(
        tmp_path,
        credentials=_Credentials(),
        llm_client=llm,
        operator_queue=command_queue,
        approval=type("Approval", (), {"approve": lambda _self, _action: True})(),
    )
    result = runner.run()
    return type(
        "GuidanceDemoResult",
        (),
        {
            "stop_reason": result.stop_reason,
            "prompts": llm.prompts,
            "guidance_was_queued_during_operation": True,
            "blocked_operation_was_not_interrupted": llm.calls == 2,
        },
    )()


def test_queued_guidance_changes_next_repair_prompt(tmp_path: Path) -> None:
    result = run_guidance_demo(tmp_path)

    assert result.stop_reason is StopReason.SUCCESS
    assert result.guidance_was_queued_during_operation is True
    assert '"guidance_event_summaries": ["preserve the public API"]' in result.prompts[-1]
    assert result.blocked_operation_was_not_interrupted is True
