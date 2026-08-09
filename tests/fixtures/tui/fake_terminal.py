from __future__ import annotations

import threading


class FakeStream:
    def __init__(self, *, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


class FakePromptSession:
    def __init__(self, responses: list[str] | None = None, *, pending_text: str = "") -> None:
        self._responses = iter(responses or [])
        self.pending_text = pending_text

    async def prompt_async(self, _prompt: str) -> str:
        try:
            return next(self._responses)
        except StopIteration as exc:
            raise EOFError from exc


class FakeTickSource:
    def __init__(self, ticks: list[int]) -> None:
        self._ticks = iter(ticks)
        self.ticks: list[int] = []
        self.sleep_calls = 0

    def next_tick(self) -> int:
        tick = next(self._ticks)
        self.ticks.append(tick)
        return tick

    def sleep(self) -> None:
        self.sleep_calls += 1


class FakeConsole:
    def __init__(self) -> None:
        self.lines: list[tuple[object, str | None]] = []
        self.status_updates: list[object] = []
        self.status_exits = 0
        self.printed = threading.Event()

    def print(self, value: object, *, style: str | None = None) -> None:
        self.lines.append((value, style))
        self.printed.set()

    def status(self, value: object, **_kwargs: object):
        self.status_updates.append(value)
        return _FakeStatus(self.status_updates, self)


class _FakeStatus:
    def __init__(self, updates: list[object], console: FakeConsole) -> None:
        self._updates = updates
        self._console = console

    def __enter__(self) -> _FakeStatus:
        return self

    def __exit__(self, *_args: object) -> None:
        self._console.status_exits += 1
        return None

    def update(self, value: object) -> None:
        self._updates.append(value)
