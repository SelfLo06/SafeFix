"""Presentation-only guided repair console for interactive terminals."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
import queue
import threading
from typing import Protocol

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console

from .events import SessionEvent
from .models import SessionResult
from .operator import OperatorCommandQueue
from .runner import SessionRunner


@dataclass(frozen=True)
class TerminalCapabilities:
    interactive: bool
    color: bool
    unicode: bool
    animation: bool


def terminal_capabilities(
    stdout: object,
    stderr: object,
    environ: Mapping[str, str],
    no_animation: bool,
    test_mode: bool,
) -> TerminalCapabilities:
    interactive = bool(stdout.isatty()) and bool(stderr.isatty())  # type: ignore[attr-defined]
    dumb_terminal = environ.get("TERM") == "dumb"
    color = interactive and not dumb_terminal and "NO_COLOR" not in environ
    unicode = interactive and not dumb_terminal
    animation = interactive and color and unicode and not no_animation and not test_mode
    return TerminalCapabilities(interactive, color, unicode, animation)


class TuiEventSink:
    """Move already-sanitized runner events across the worker/UI boundary."""

    def __init__(
        self,
        events: queue.Queue[SessionEvent],
        on_emit: Callable[[], None] | None = None,
    ) -> None:
        self._events = events
        self._on_emit = on_emit

    def emit(self, event: SessionEvent) -> None:
        self._events.put(event)
        if self._on_emit is not None:
            self._on_emit()


@dataclass(frozen=True)
class RenderedTranscriptEntry:
    text: str
    style: str


@dataclass(frozen=True)
class RenderedFrame:
    text: str


_EVENT_LABELS = {
    "pytest": "TEST",
    "guardrail": "GUARD",
    "patch": "PATCH",
    "tool": "TOOL",
    "model-call": "MODEL",
    "control": "CONTROL",
    "guidance": "GUIDANCE",
    "terminal": "STATUS",
}
_EVENT_STYLES = {
    "pytest": "cyan",
    "guardrail": "yellow",
    "patch": "magenta",
    "tool": "blue",
    "model-call": "blue",
    "control": "green",
    "guidance": "green",
    "terminal": "bold",
}


def render_event(
    event: SessionEvent, capabilities: TerminalCapabilities
) -> RenderedTranscriptEntry:
    """Project the safe summary into one scrollback transcript line."""
    payload = event.safe_payload
    summary = payload.get("summary", event.kind)
    label = _EVENT_LABELS.get(event.kind, event.kind.upper())
    marker = " ✓" if capabilities.unicode and event.kind == "guardrail" else ""
    return RenderedTranscriptEntry(
        text=f"[{label}]{marker} {summary}",
        style=_EVENT_STYLES.get(event.kind, "white") if capabilities.color else "",
    )


def animation_frames(
    event: SessionEvent, capabilities: TerminalCapabilities
) -> tuple[RenderedFrame, ...]:
    entry = render_event(event, capabilities)
    if capabilities.animation and event.safe_payload.get("status") == "running":
        return tuple(RenderedFrame(f"{entry.text}{suffix}") for suffix in (".", "..", "..."))
    return (RenderedFrame(entry.text),)


class _TickSource(Protocol):
    def next_tick(self) -> object:
        """Advance a deterministic animation clock."""


class GuidedRepairConsole:
    """Render queued events while submitting operator input to the Harness queue."""

    def __init__(
        self,
        command_queue: OperatorCommandQueue,
        controller_factory: Callable[[TuiEventSink, OperatorCommandQueue], SessionRunner],
        input_factory: Callable[[], PromptSession],
        console: Console,
        capabilities: TerminalCapabilities,
        tick_source: _TickSource,
    ) -> None:
        self.command_queue = command_queue
        self._controller_factory = controller_factory
        self._input_factory = input_factory
        self._console = console
        self._capabilities = capabilities
        self._tick_source = tick_source
        self._events: queue.Queue[SessionEvent] = queue.Queue()
        self._controller_active = False
        self._result: SessionResult | None = None
        self.rendered_event_sequences: list[int] = []

    def publish(self, event: SessionEvent) -> None:
        self._events.put(event)

    def drain_events_once(self) -> None:
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                return
            self.rendered_event_sequences.append(event.sequence)
            entry = render_event(event, self._capabilities)
            frames = animation_frames(event, self._capabilities)
            self._console.print(f"Status: {event.phase.value}", style="bold")
            if len(frames) > 1:
                first_tick = self._tick_source.next_tick()
                with self._console.status(frames[int(first_tick) % len(frames)].text) as status:
                    for _ in range(len(frames) - 1):
                        tick = self._tick_source.next_tick()
                        status.update(frames[int(tick) % len(frames)].text)
            self._console.print(entry.text, style=entry.style)

    def run(self) -> SessionResult:
        if self._capabilities.interactive:
            return asyncio.run(self._run_interactive())
        return self._run_without_input()

    def _run_without_input(self) -> SessionResult:
        controller = self._controller_factory(TuiEventSink(self._events), self.command_queue)

        def run_controller() -> None:
            self._controller_active = True
            try:
                self._result = controller.run()
            finally:
                self._controller_active = False

        worker = threading.Thread(target=run_controller, name="safefix-runner")
        worker.start()
        worker.join()
        self.drain_events_once()
        assert self._result is not None
        return self._result

    async def _run_interactive(self) -> SessionResult:
        loop = asyncio.get_running_loop()
        event_ready = asyncio.Event()
        controller_finished = asyncio.Event()

        def notify_event() -> None:
            loop.call_soon_threadsafe(event_ready.set)

        controller = self._controller_factory(
            TuiEventSink(self._events, notify_event), self.command_queue
        )

        def run_controller() -> None:
            self._controller_active = True
            try:
                self._result = controller.run()
            finally:
                self._controller_active = False
                loop.call_soon_threadsafe(controller_finished.set)

        worker = threading.Thread(target=run_controller, name="safefix-runner")
        with patch_stdout(raw=True):
            worker.start()
            input_task: asyncio.Task[None] | None = asyncio.create_task(self._read_input())
            event_task = asyncio.create_task(event_ready.wait())
            finished_task = asyncio.create_task(controller_finished.wait())
            try:
                while not finished_task.done():
                    waiting: set[asyncio.Task[None]] = {event_task, finished_task}
                    if input_task is not None:
                        waiting.add(input_task)
                    done, _pending = await asyncio.wait(
                        waiting, return_when=asyncio.FIRST_COMPLETED
                    )
                    if event_task in done:
                        event_ready.clear()
                        self.drain_events_once()
                        event_task = asyncio.create_task(event_ready.wait())
                    if input_task is not None and input_task in done:
                        input_task = None
                self.drain_events_once()
            finally:
                event_task.cancel()
                with suppress(asyncio.CancelledError):
                    await event_task
                if input_task is not None:
                    input_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await input_task

        worker.join()
        assert self._result is not None
        return self._result

    async def _read_input(self, prompt: PromptSession | None = None) -> None:
        prompt = prompt or self._input_factory()
        while True:
            try:
                line = await prompt.prompt_async("safefix> ")
            except (EOFError, KeyboardInterrupt, OSError):
                if self._controller_active:
                    self.command_queue.submit_text("/stop")
                return
            self.command_queue.submit_text(line)
