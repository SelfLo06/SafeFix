from .base import HTTPTransport, LLMResponseError, LLMTransportError


DEFAULT_MODEL_TIMEOUT_SECONDS = 120
DEFAULT_MODEL_TEMPERATURE = 0.2


class OpenAICompatibleClient:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        transport: HTTPTransport,
        timeout: float = DEFAULT_MODEL_TIMEOUT_SECONDS,
        temperature: float = DEFAULT_MODEL_TEMPERATURE,
    ) -> None:
        if not 0.0 <= temperature <= 2.0:
            raise ValueError("temperature must be between 0.0 and 2.0")
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._model = model
        self._api_key = api_key
        self._transport = transport
        self._timeout = timeout
        self._temperature = temperature

    def complete(self, prompt: str) -> str:
        try:
            response = self._transport.post(
                self._url,
                {
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                {
                    "model": self._model,
                    "messages": [{"role": "user", "content": prompt}],
                    # Keep repair output deterministic. The OpenAI-compatible
                    # contract has no portable thinking duration field.
                    "temperature": self._temperature,
                },
                self._timeout,
            )
        except OSError as exc:
            raise LLMTransportError(str(exc)) from exc

        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError("invalid OpenAI-compatible response") from exc
        if not isinstance(content, str):
            raise LLMResponseError("invalid OpenAI-compatible response")
        return content
