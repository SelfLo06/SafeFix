import tempfile

import pytest

from safefix.llm.base import LLMTransportError
from safefix.llm.openai_compatible import OpenAICompatibleClient
from safefix.llm.roles import ModelClientFactory
from safefix.models import ModelRole, ModelRoleConfig


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
                "temperature": 0.2,
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


def test_openai_client_uses_a_120_second_default_timeout():
    transport = FakeTransport(
        response={"choices": [{"message": {"content": "ok"}}]}
    )
    client = OpenAICompatibleClient(
        base_url="https://llm.example/v1",
        model="repair-model",
        api_key="test-key",
        transport=transport,
    )

    assert client.complete("repair the failing test") == "ok"
    assert transport.requests[0][3] == 120


def test_openai_client_accepts_an_explicit_temperature():
    transport = FakeTransport(response={"choices": [{"message": {"content": "ok"}}]})
    client = OpenAICompatibleClient(
        base_url="https://llm.example/v1", model="repair-model", api_key="test-key",
        transport=transport, temperature=0.7,
    )
    client.complete("prompt")
    assert transport.requests[0][2]["temperature"] == 0.7


def test_openai_client_rejects_temperature_outside_provider_range():
    with pytest.raises(ValueError, match="temperature"):
        OpenAICompatibleClient(
            base_url="https://llm.example/v1", model="repair-model", api_key="test-key",
            transport=FakeTransport(), temperature=2.1,
        )


def test_curl_transport_keeps_api_key_out_of_process_arguments(monkeypatch):
    from types import SimpleNamespace

    from safefix.llm.roles import CurlHTTPTransport

    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["config"] = kwargs["input"]
        return SimpleNamespace(returncode=0, stdout='{"ok":true}\n200', stderr="")

    monkeypatch.setattr("safefix.llm.roles.subprocess.run", fake_run)

    result = CurlHTTPTransport().post(
        "https://llm.example/v1/chat/completions",
        {"Authorization": "Bearer secret-key", "Content-Type": "application/json"},
        {"model": "test-model"},
        42,
    )

    assert result == {"ok": True}
    assert seen["command"][:2] == ["curl", "--disable"]
    assert all("secret-key" not in argument for argument in seen["command"])
    assert "Authorization: Bearer secret-key" in seen["config"]
    assert "--max-time" in seen["command"]


def test_curl_transport_rejects_newlines_in_curl_config_values():
    from safefix.llm.roles import CurlHTTPTransport

    with pytest.raises(OSError, match="invalid curl configuration value"):
        CurlHTTPTransport().post(
            "https://llm.example/v1\ntrace = /tmp/leak",
            {"Authorization": "Bearer test-key"},
            {"model": "test-model"},
            42,
        )


def test_curl_transport_rejects_newlines_in_authorization_header():
    from safefix.llm.roles import CurlHTTPTransport

    with pytest.raises(OSError, match="invalid curl configuration value"):
        CurlHTTPTransport().post(
            "https://llm.example/v1",
            {"Authorization": "Bearer test-key\ntrace = /tmp/leak"},
            {"model": "test-model"},
            42,
        )


def test_curl_transport_closes_request_file_before_invoking_curl(monkeypatch):
    from types import SimpleNamespace

    from safefix.llm.roles import CurlHTTPTransport

    original_named_temporary_file = tempfile.NamedTemporaryFile
    request_file = None

    def create_request_file(*args, **kwargs):
        nonlocal request_file
        request_file = original_named_temporary_file(*args, **kwargs)
        return request_file

    def fake_run(*args, **kwargs):
        assert request_file is not None and request_file.closed
        return SimpleNamespace(returncode=0, stdout='{"ok":true}\n200', stderr="")

    monkeypatch.setattr("safefix.llm.roles.tempfile.NamedTemporaryFile", create_request_file)
    monkeypatch.setattr("safefix.llm.roles.subprocess.run", fake_run)

    assert CurlHTTPTransport().post(
        "https://llm.example/v1", {"Authorization": "Bearer test-key"}, {"model": "test-model"}, 42
    ) == {"ok": True}


def test_curl_transport_reports_curl_timeout_and_uses_configured_deadline(monkeypatch):
    from types import SimpleNamespace

    from safefix.llm.roles import CurlHTTPTransport

    seen = {}

    def fake_run(*args, **kwargs):
        seen["timeout"] = kwargs["timeout"]
        return SimpleNamespace(returncode=28, stdout="\n000", stderr="timed out")

    monkeypatch.setattr("safefix.llm.roles.subprocess.run", fake_run)

    with pytest.raises(OSError, match="request timed out"):
        CurlHTTPTransport().post(
            "https://llm.example/v1", {"Authorization": "Bearer test-key"}, {"model": "test-model"}, 42
        )

    assert seen["timeout"] == 42


def test_production_client_uses_curl_transport():
    from safefix.cli import production_client
    from safefix.llm.roles import CurlHTTPTransport

    client = production_client(
        base_url="https://llm.example/v1",
        model="test-model",
        api_key="test-key",
    )

    assert isinstance(client._transport, CurlHTTPTransport)


def test_model_client_factory_reads_only_requested_role_credential():
    environ = {
        "SAFEFIX_TEST_API_KEY": "test-key",
        "SAFEFIX_REPAIR_API_KEY": "repair-key",
    }
    transport = FakeTransport(
        response={"choices": [{"message": {"content": "ok"}}]}
    )
    factory = ModelClientFactory(transport=transport)

    client = factory.create(
        ModelRoleConfig(
            role=ModelRole.TEST,
            base_url="https://test.example/v1",
            model="test-model",
            credential_env="SAFEFIX_TEST_API_KEY",
        ),
        environ,
    )

    assert client.complete("prompt") == "ok"
    assert transport.requests[0][1]["Authorization"] == "Bearer test-key"


def test_model_client_factory_uses_configured_credential_environment():
    environ = {
        "SAFEFIX_TEST_API_KEY": "test-key",
        "SAFEFIX_REPAIR_API_KEY": "repair-key",
    }
    transport = FakeTransport(
        response={"choices": [{"message": {"content": "ok"}}]}
    )

    client = ModelClientFactory(transport=transport).create(
        ModelRoleConfig(
            role=ModelRole.TEST,
            base_url="https://test.example/v1",
            model="test-model",
            credential_env="SAFEFIX_REPAIR_API_KEY",
        ),
        environ,
    )

    assert client.complete("prompt") == "ok"
    assert transport.requests[0][1]["Authorization"] == "Bearer repair-key"
