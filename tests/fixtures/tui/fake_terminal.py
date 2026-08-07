from __future__ import annotations


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
        self.sleep_calls = 0

    def next_tick(self) -> int:
        return next(self._ticks)

    def sleep(self) -> None:
        self.sleep_calls += 1


class FakeConsole:
    def __init__(self) -> None:
        self.lines: list[tuple[object, str | None]] = []

    def print(self, value: object, *, style: str | None = None) -> None:
        self.lines.append((value, style))
