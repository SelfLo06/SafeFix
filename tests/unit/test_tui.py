from __future__ import annotations

import asyncio
import queue
import threading

import pytest

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


class PendingPrompt:
    def __init__(self) -> None:
        self.started = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending: asyncio.Future[None] | None = None

    async def prompt_async(self, _prompt: str) -> str:
        self._loop = asyncio.get_running_loop()
        self._pending = self._loop.create_future()
        self.started.set()
        await self._pending
        raise EOFError

    def release(self) -> None:
        assert self._loop is not None
        assert self._pending is not None
        if not self._pending.done():
            self._loop.call_soon_threadsafe(self._pending.set_result, None)


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


def test_console_drains_worker_events_while_prompt_is_pending() -> None:
    rendered = threading.Event()
    event = SessionEvent(1, "2026-08-07T00:00:00Z", Phase.READY, "guardrail", {"summary": "safe"})

    class EventController:
        def run(self) -> SessionResult:
            sink.emit(event)
            rendered.wait()
            return SessionResult(StopReason.REQUESTED)

    prompt = PendingPrompt()
    command_queue = OperatorCommandQueue()
    console_output = FakeConsole()
    sink: TuiEventSink

    def controller_factory(event_sink: TuiEventSink, _queue: OperatorCommandQueue) -> EventController:
        nonlocal sink
        sink = event_sink
        return EventController()

    console = GuidedRepairConsole(
        command_queue,
        controller_factory,
        lambda: prompt,
        console_output,
        TerminalCapabilities(True, True, True, False),
        FakeTickSource([]),
    )

    worker = threading.Thread(target=console.run, daemon=True)
    worker.start()
    assert prompt.started.wait(timeout=0.5)
    try:
        assert console_output.printed.wait(timeout=0.5)
        assert console_output.lines[-1][0] == "[GUARD] ✓ safe"
        rendered.set()
    finally:
        rendered.set()
        prompt.release()
        worker.join(timeout=1)


def test_console_returns_when_runner_finishes_with_pending_prompt() -> None:
    prompt = PendingPrompt()
    console = console_with_fake_prompt([], prompt=prompt)

    result: list[SessionResult] = []
    worker = threading.Thread(target=lambda: result.append(console.run()), daemon=True)
    worker.start()
    assert prompt.started.wait(timeout=0.5)
    try:
        worker.join(timeout=0.5)
        assert not worker.is_alive()
    finally:
        prompt.release()
        worker.join(timeout=0.5)

    assert result == [SessionResult(StopReason.REQUESTED)]


def test_non_interactive_console_reraises_controller_exception_after_cleanup() -> None:
    failure = RuntimeError("runner failed")

    class RaisingController:
        def run(self) -> SessionResult:
            raise failure

    console = GuidedRepairConsole(
        OperatorCommandQueue(),
        lambda _sink, _queue: RaisingController(),
        FakePromptSession,
        FakeConsole(),
        TerminalCapabilities(False, False, False, False),
        FakeTickSource([]),
    )

    with pytest.raises(RuntimeError) as raised:
        console.run()

    assert raised.value is failure


def test_interactive_console_reraises_controller_exception_after_cleanup() -> None:
    failure = RuntimeError("runner failed")

    class RaisingController:
        def run(self) -> SessionResult:
            raise failure

    console = GuidedRepairConsole(
        OperatorCommandQueue(),
        lambda _sink, _queue: RaisingController(),
        FakePromptSession,
        FakeConsole(),
        TerminalCapabilities(True, True, True, False),
        FakeTickSource([]),
    )

    with pytest.raises(RuntimeError) as raised:
        console.run()

    assert raised.value is failure
