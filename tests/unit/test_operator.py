from collections import deque
import threading

from safefix.operator import GuidanceBuffer, OperatorCommand, OperatorCommandQueue


def test_operator_commands_are_parsed_without_dispatch_authority() -> None:
    queue = OperatorCommandQueue()

    for text, kind in (
        ("/pause", "pause"),
        ("/resume", "resume"),
        ("/stop", "stop"),
        ("/status", "status"),
        ("/approve", "approve"),
        ("/deny", "deny"),
    ):
        queue.submit_text(text)

    assert queue.drain_ready_commands(pending_approval=True) == (
        OperatorCommand("pause"),
        OperatorCommand("resume"),
        OperatorCommand("stop"),
        OperatorCommand("status"),
        OperatorCommand("approve"),
        OperatorCommand("deny"),
    )


def test_approval_commands_are_noops_without_pending_approval() -> None:
    queue = OperatorCommandQueue()
    queue.submit_text("/approve")
    queue.submit_text("/deny")

    assert queue.drain_ready_commands(pending_approval=False) == ()


def test_unknown_slash_and_direct_tool_text_are_guidance() -> None:
    queue = OperatorCommandQueue()
    queue.submit_text("/apply_patch src/app.py")
    queue.submit_text("run shell rm -rf project")

    assert queue.drain_ready_guidance() == (
        "/apply_patch src/app.py",
        "run shell rm -rf project",
    )
    assert queue.drain_ready_commands(pending_approval=True) == ()


def test_guidance_is_drained_only_at_ready_boundary() -> None:
    queue = OperatorCommandQueue()
    queue.submit_text("preserve the public return type")

    assert queue.drain_ready_guidance() == ("preserve the public return type",)
    assert queue.drain_ready_guidance() == ()


def test_guidance_buffer_bounds_items_and_total_characters() -> None:
    buffer = GuidanceBuffer(max_items=2, max_chars=10)
    buffer.enqueue("first")
    buffer.enqueue("second")
    buffer.enqueue("third")

    summaries = buffer.summaries()
    assert len(summaries) <= 2
    assert sum(len(item) for item in summaries) <= 10
    assert summaries[-1] == "third"

    drained = buffer.drain_for_ready()
    assert drained == summaries
    assert buffer.summaries() == ()


def test_guidance_submitted_while_ready_drain_clears_remains_queued() -> None:
    class PausingClearDeque(deque[str]):
        def __init__(self, items: deque[str]) -> None:
            super().__init__(items)
            self.clear_started = threading.Event()
            self.allow_clear = threading.Event()

        def clear(self) -> None:
            self.clear_started.set()
            assert self.allow_clear.wait(timeout=0.5)
            super().clear()

    guidance = GuidanceBuffer()
    queue = OperatorCommandQueue(guidance=guidance)
    queue.submit_text("existing guidance")
    paused_items = PausingClearDeque(guidance._items)
    guidance._items = paused_items

    drained: list[tuple[str, ...]] = []
    drainer = threading.Thread(target=lambda: drained.append(queue.drain_ready_guidance()))
    drainer.start()
    assert paused_items.clear_started.wait(timeout=0.5)

    producer = threading.Thread(target=lambda: queue.submit_text("concurrent guidance"))
    producer.start()
    paused_items.allow_clear.set()
    drainer.join(timeout=0.5)
    producer.join(timeout=0.5)

    assert not drainer.is_alive()
    assert not producer.is_alive()
    assert drained == [("existing guidance",)]
    assert queue.drain_ready_guidance() == ("concurrent guidance",)
