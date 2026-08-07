"""Presentation-only guided repair console for interactive terminals."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
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

    def __init__(self, events: queue.Queue[SessionEvent]) -> None:
        self._events = events

    def emit(self, event: SessionEvent) -> None:
        self._events.put(event)


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
            for frame in frames:
                if len(frames) > 1:
                    self._tick_source.next_tick()
                self._console.print(frame.text, style=entry.style)

    def run(self) -> SessionResult:
        sink = TuiEventSink(self._events)
        controller = self._controller_factory(sink, self.command_queue)

        def run_controller() -> None:
            self._controller_active = True
            try:
                self._result = controller.run()
            finally:
                self._controller_active = False

        worker = threading.Thread(target=run_controller, name="safefix-runner")
        worker.start()
        if self._capabilities.interactive:
            with patch_stdout(raw=True):
                asyncio.run(self._read_input())
        worker.join()
        self.drain_events_once()
        assert self._result is not None
        return self._result

    async def _read_input(self) -> None:
        prompt = self._input_factory()
        while True:
            try:
                line = await prompt.prompt_async("safefix> ")
            except (EOFError, KeyboardInterrupt, OSError):
                if self._controller_active:
                    self.command_queue.submit_text("/stop")
                return
            self.command_queue.submit_text(line)
