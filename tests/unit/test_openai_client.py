import pytest

from safefix.llm.base import LLMTransportError
from safefix.llm.openai_compatible import OpenAICompatibleClient
from safefix.llm.roles import ModelClientFactory
from safefix.models import ModelRole, ModelRoleConfig


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        del self.values[(service, username)]


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


def test_model_client_factory_reads_only_requested_role_credential():
    keyring = FakeKeyring()
    keyring.set_password("safefix-test", "api_key", "test-key")
    keyring.set_password("safefix-repair", "api_key", "repair-key")
    transport = FakeTransport(
        response={"choices": [{"message": {"content": "ok"}}]}
    )
    factory = ModelClientFactory(transport=transport)

    client = factory.create(
        ModelRoleConfig(
            role=ModelRole.TEST,
            base_url="https://test.example/v1",
            model="test-model",
            keyring_service="safefix-test",
        ),
        keyring,
    )

    assert client.complete("prompt") == "ok"
    assert transport.requests[0][1]["Authorization"] == "Bearer test-key"


def test_model_client_factory_uses_role_service_when_config_service_is_mismatched():
    keyring = FakeKeyring()
    keyring.set_password("safefix-test", "api_key", "test-key")
    keyring.set_password("safefix-repair", "api_key", "repair-key")
    transport = FakeTransport(
        response={"choices": [{"message": {"content": "ok"}}]}
    )

    client = ModelClientFactory(transport=transport).create(
        ModelRoleConfig(
            role=ModelRole.TEST,
            base_url="https://test.example/v1",
            model="test-model",
            keyring_service="safefix-repair",
        ),
        keyring,
    )

    assert client.complete("prompt") == "ok"
    assert transport.requests[0][1]["Authorization"] == "Bearer test-key"
