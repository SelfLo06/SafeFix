from .base import HTTPTransport, LLMResponseError, LLMTransportError


class OpenAICompatibleClient:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        transport: HTTPTransport,
        timeout: float = 30,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._model = model
        self._api_key = api_key
        self._transport = transport
        self._timeout = timeout

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
