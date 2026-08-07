from __future__ import annotations

import asyncio
import queue

from safefix.events import SessionEvent
from safefix.models import Phase, SessionResult, StopReason
from safefix.operator import OperatorCommand, OperatorCommandQueue
from safefix.tui import GuidedRepairConsole, TerminalCapabilities, TuiEventSink
from tests.fixtures.tui.fake_terminal import FakeConsole, FakePromptSession, FakeTickSource


class FakeController:
    def __init__(self) -> None:
        self.tool_calls: tuple[object, ...] = ()

    def run(self) -> SessionResult:
        return SessionResult(StopReason.REQUESTED)


def console_with_fake_prompt(responses: list[str], *, prompt: FakePromptSession | None = None) -> GuidedRepairConsole:
    command_queue = OperatorCommandQueue()
    controller = FakeController()
    console = GuidedRepairConsole(command_queue, lambda _sink, _queue: controller, lambda: prompt or FakePromptSession(responses), FakeConsole(), TerminalCapabilities(True, True, True, False), FakeTickSource([]))
    console.fake_controller = controller
    return console


def test_tui_event_sink_forwards_typed_events_to_thread_safe_queue() -> None:
    events: queue.Queue[SessionEvent] = queue.Queue()
    event = SessionEvent(1, "2026-08-07T00:00:00Z", Phase.READY, "control", {"summary": "ready"})
    TuiEventSink(events).emit(event)
    assert events.get_nowait() == event


def test_console_queues_guidance_and_controls_without_tool_dispatch() -> None:
    console = console_with_fake_prompt(["preserve parse return type", "/stop"])
    console.run()
    assert console.command_queue.drain_ready_guidance() == ("preserve parse return type",)
    assert console.command_queue.drain_ready_commands() == (OperatorCommand("stop"),)
    assert console.fake_controller.tool_calls == ()


def test_background_event_preserves_pending_prompt_buffer() -> None:
    prompt = FakePromptSession(pending_text="keep public API")
    console = console_with_fake_prompt([], prompt=prompt)
    console.publish(SessionEvent(1, "2026-08-07T00:00:00Z", Phase.READY, "guardrail", {"summary": "safe"}))
    console.drain_events_once()
    assert prompt.pending_text == "keep public API"


def test_eof_queues_safe_stop_only_while_controller_is_active() -> None:
    console = console_with_fake_prompt([])
    console._controller_active = True
    asyncio.run(console._read_input())
    assert console.command_queue.drain_ready_commands() == (OperatorCommand("stop"),)


def test_terminal_close_queues_safe_stop_only_while_controller_is_active() -> None:
    class ClosingPrompt:
        async def prompt_async(self, _prompt: str) -> str:
            raise OSError("terminal closed")

    console = console_with_fake_prompt([], prompt=ClosingPrompt())
    console._controller_active = True
    asyncio.run(console._read_input())
    assert console.command_queue.drain_ready_commands() == (OperatorCommand("stop"),)
