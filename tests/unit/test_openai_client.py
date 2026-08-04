import pytest

from safefix.llm.base import LLMTransportError
from safefix.llm.openai_compatible import OpenAICompatibleClient


class FakeTransport:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.requests = []

    def post(self, url, headers, json_body, timeout):
        self.requests.append((url, headers, json_body, timeout))
        if self.error is not None:
            raise self.error
        return self.response


def test_openai_client_sends_expected_request_through_injected_transport():
    transport = FakeTransport(
        response={"choices": [{"message": {"content": "{\"tool\": \"finish\"}"}}]}
    )
    client = OpenAICompatibleClient(
        base_url="https://llm.example/v1",
        model="repair-model",
        api_key="test-key",
        transport=transport,
        timeout=12,
    )

    assert client.complete("repair the failing test") == '{"tool": "finish"}'
    assert transport.requests == [
        (
            "https://llm.example/v1/chat/completions",
            {"Authorization": "Bearer test-key", "Content-Type": "application/json"},
            {
                "model": "repair-model",
                "messages": [{"role": "user", "content": "repair the failing test"}],
            },
            12,
        )
    ]


def test_openai_client_maps_transport_os_error_to_llm_transport_error():
    transport = FakeTransport(error=OSError("connection refused"))
    client = OpenAICompatibleClient(
        base_url="https://llm.example/v1",
        model="repair-model",
        api_key="test-key",
        transport=transport,
    )

    with pytest.raises(LLMTransportError, match="connection refused") as error:
        client.complete("repair the failing test")

    assert isinstance(error.value.__cause__, OSError)
