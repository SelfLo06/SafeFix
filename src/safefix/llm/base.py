from typing import Any, Protocol


class LLMClient(Protocol):
    def complete(self, prompt: str) -> str:
        """Return one assistant response for a prompt."""


class HTTPTransport(Protocol):
    def post(
        self, url: str, headers: dict[str, str], json_body: dict[str, Any], timeout: float
    ) -> dict[str, Any]:
        """Send one JSON POST request."""


class LLMError(RuntimeError):
    """Base error for LLM client failures."""


class LLMTransportError(LLMError):
    """Raised when the injected HTTP transport cannot send a request."""


class LLMResponseError(LLMError):
    """Raised when a response is not an OpenAI-compatible completion."""
