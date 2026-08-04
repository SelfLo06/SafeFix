from collections.abc import Iterable


class ScriptExhaustedError(RuntimeError):
    """Raised when a scripted MockLLM has no response left."""


class MockLLM:
    def __init__(self, responses: Iterable[str]) -> None:
        self._responses = iter(responses)

    def complete(self, prompt: str) -> str:
        try:
            return next(self._responses)
        except StopIteration as exc:
            raise ScriptExhaustedError("scripted responses exhausted") from exc
