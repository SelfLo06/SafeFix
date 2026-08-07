from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import threading

from .events import sanitize_summary


CONTROL_COMMANDS = frozenset({"pause", "resume", "stop", "status", "approve", "deny"})
GUIDANCE = "guidance"


@dataclass(frozen=True)
class OperatorCommand:
    kind: str
    text: str | None = None


class GuidanceBuffer:
    """Keep only bounded, redacted operator guidance summaries."""

    def __init__(self, max_items: int = 20, max_chars: int = 4000) -> None:
        if max_items <= 0:
            raise ValueError("max_items must be positive")
        if max_chars <= 0:
            raise ValueError("max_chars must be positive")
        self._max_items = max_items
        self._max_chars = max_chars
        self._items: deque[str] = deque()
        self._lock = threading.Lock()

    def enqueue(self, text: str) -> None:
        summary = sanitize_summary(text, max_chars=self._max_chars).strip()
        if not summary:
            return
        with self._lock:
            self._items.append(summary)
            while len(self._items) > self._max_items or self._char_count() > self._max_chars:
                self._items.popleft()

    def drain_for_ready(self) -> tuple[str, ...]:
        with self._lock:
            drained = tuple(self._items)
            self._items.clear()
            return drained

    def summaries(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._items)

    def _char_count(self) -> int:
        return sum(len(item) for item in self._items)


class OperatorCommandQueue:
    """Queue controls separately from guidance; it never dispatches tools."""

    def __init__(
        self,
        *,
        guidance: GuidanceBuffer | None = None,
    ) -> None:
        self._commands: deque[OperatorCommand] = deque()
        self._guidance = guidance or GuidanceBuffer()

    def submit_text(self, text: str) -> OperatorCommand:
        command = self.parse_text(text)
        if command.kind == GUIDANCE:
            self._guidance.enqueue(command.text or "")
        else:
            self._commands.append(command)
        return command

    def submit(self, command: OperatorCommand) -> None:
        if command.kind == GUIDANCE:
            self._guidance.enqueue(command.text or "")
        elif command.kind in CONTROL_COMMANDS:
            self._commands.append(command)
        else:
            self._guidance.enqueue(command.text or command.kind)

    @staticmethod
    def parse_text(text: str) -> OperatorCommand:
        normalized = text.strip()
        if normalized.startswith("/"):
            command_name = normalized[1:].lower()
            if command_name in CONTROL_COMMANDS:
                return OperatorCommand(command_name)
        return OperatorCommand(GUIDANCE, normalized)

    def drain_ready_commands(
        self,
        *,
        pending_approval: bool = False,
        include_ignored_approval: bool = False,
    ) -> tuple[OperatorCommand, ...]:
        commands: list[OperatorCommand] = []
        while self._commands:
            command = self._commands.popleft()
            if command.kind in {"approve", "deny"} and not pending_approval:
                if not include_ignored_approval:
                    continue
            commands.append(command)
        return tuple(commands)

    def drain_ready_guidance(self) -> tuple[str, ...]:
        return self._guidance.drain_for_ready()

    def guidance_summaries(self) -> tuple[str, ...]:
        return self._guidance.summaries()
