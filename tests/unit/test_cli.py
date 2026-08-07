from pathlib import Path

import pytest

from safefix.approval import ApprovalProvider
from safefix.models import Config, SessionResult, StopReason
from safefix.credentials import CredentialsResolver
from safefix.models import ModelRole


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        del self.values[(service, username)]


class FakeCredentials:
    def __init__(self) -> None:
        self.value: str | None = None

    def set(self, value: str) -> None:
        self.value = value

    def status(self) -> bool:
        return self.value is not None

    def clear(self) -> None:
        self.value = None

    def get(self) -> str:
        assert self.value is not None
        return self.value

    def for_role(self, _role: ModelRole) -> "FakeCredentials":
        return self


class FakeRunner:
    def __init__(self, result: SessionResult) -> None:
        self._result = result

    def run(self) -> SessionResult:
        return self._result


def test_run_command_caches_validated_boundaries_for_runner(tmp_path: Path) -> None:
    from safefix.cli import main

    class CountingCredentials(FakeCredentials):
        def __init__(self) -> None:
            super().__init__()
            self.get_calls = 0

        def get(self) -> str:
            self.get_calls += 1
            return super().get()

    credentials = CountingCredentials()
    credentials.set("stored-test-key")
    config = Config(base_url="https://llm.example/v1", model="repair-model")
    config_loader_calls = 0
    seen: dict[str, object] = {}

    def config_loader(root: Path, overrides: dict[str, object], require_llm: bool) -> Config:
        nonlocal config_loader_calls
        config_loader_calls += 1
        return config

    def runner_factory(project_root: Path, **kwargs: object) -> FakeRunner:
        runner_config_loader = kwargs.get("config_loader", config_loader)
        runner_credentials = kwargs["credentials"]
        seen["runner_config"] = runner_config_loader(
            project_root, kwargs["cli_overrides"], require_llm=True
        )
        seen["runner_key"] = runner_credentials.get()
        return FakeRunner(SessionResult(stop_reason=StopReason.SUCCESS))

    assert main(
        ["run", str(tmp_path)],
        credentials_factory=lambda: credentials,
        config_loader=config_loader,
        runner_factory=runner_factory,
        client_factory=lambda **kwargs: object(),
    ) == 0

    assert config_loader_calls == 1
    assert credentials.get_calls == 1
    assert seen == {
        "runner_config": config,
        "runner_key": "stored-test-key",
    }


def test_run_command_passes_config_overrides(tmp_path: Path) -> None:
    from safefix.cli import main

    credentials = FakeCredentials()
    credentials.set("stored-test-key")
    seen: dict[str, object] = {}
    config = Config(base_url="https://llm.example/v1", model="repair-model")

    def runner_factory(project_root: Path, **kwargs: object) -> FakeRunner:
        seen["project_root"] = project_root
        seen.update(kwargs)
        return FakeRunner(SessionResult(stop_reason=StopReason.SUCCESS))

    def client_factory(**kwargs: object) -> object:
        seen["client"] = kwargs
        return object()

    exit_code = main(
        [
            "run",
            str(tmp_path),
            "--max-steps",
            "7",
            "--pytest-args=-q",
            "--base-url",
            "https://llm.example/v1",
            "--model",
            "repair-model",
        ],
        credentials_factory=lambda: credentials,
        config_loader=lambda root, overrides, require_llm: config,
        runner_factory=runner_factory,
        client_factory=client_factory,
    )

    assert exit_code == 0
    assert seen["project_root"] == tmp_path.resolve()
    assert seen["cli_overrides"] == {
        "max_steps": 7,
        "pytest_args": ["-q"],
        "base_url": "https://llm.example/v1",
        "model": "repair-model",
    }
    assert seen["credentials"] is not credentials
    assert seen["credentials"].get() == "stored-test-key"
    assert seen["client"] == {
        "base_url": "https://llm.example/v1",
        "model": "repair-model",
        "api_key": "stored-test-key",
    }


def test_credentials_set_status_clear(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from safefix.cli import main

    credentials = FakeCredentials()

    assert main(["credentials", "status"], credentials_factory=lambda: credentials) == 0
    assert capsys.readouterr().out == "not set\n"
    monkeypatch.setattr("getpass.getpass", lambda _: "stored-test-key")
    assert main(["credentials", "set"], credentials_factory=lambda: credentials) == 0
    assert capsys.readouterr().out == "credential stored\n"
    assert main(["credentials", "status"], credentials_factory=lambda: credentials) == 0
    assert capsys.readouterr().out == "set\n"
    assert main(["credentials", "clear"], credentials_factory=lambda: credentials) == 0
    assert capsys.readouterr().out == "credential cleared\n"


def test_noninteractive_approval_denies(tmp_path: Path) -> None:
    from safefix.cli import main

    credentials = FakeCredentials()
    credentials.set("stored-test-key")
    config = Config(base_url="https://llm.example/v1", model="repair-model")
    seen: dict[str, object] = {}

    def runner_factory(project_root: Path, **kwargs: object) -> FakeRunner:
        seen.update(kwargs)
        return FakeRunner(SessionResult(stop_reason=StopReason.REQUESTED))

    assert main(
        ["run", str(tmp_path)],
        credentials_factory=lambda: credentials,
        config_loader=lambda root, overrides, require_llm: config,
        runner_factory=runner_factory,
        client_factory=lambda **kwargs: object(),
    ) == 1
    assert isinstance(seen["approval"], ApprovalProvider)
    assert seen["approval"].approve(object()) is False


def test_exit_code_mapping_for_all_stop_reasons(tmp_path: Path) -> None:
    from safefix.cli import main

    credentials = FakeCredentials()
    credentials.set("stored-test-key")
    config = Config(base_url="https://llm.example/v1", model="repair-model")
    expected = {
        StopReason.SUCCESS: 0,
        StopReason.REQUESTED: 1,
        StopReason.MAX_STEPS: 1,
        StopReason.MAX_ROUNDS: 1,
        StopReason.NO_PROGRESS: 1,
        StopReason.OPERATOR_STOP: 1,
        StopReason.FINAL_REVIEW_REJECTED: 1,
        StopReason.TEST_PREPARATION_ERROR: 3,
        StopReason.ERROR: 3,
        StopReason.CONFIG_ERROR: 2,
    }

    for reason, exit_code in expected.items():
        assert main(
            ["run", str(tmp_path)],
            credentials_factory=lambda: credentials,
            config_loader=lambda root, overrides, require_llm: config,
            runner_factory=lambda root, **kwargs: FakeRunner(
                SessionResult(stop_reason=reason)
            ),
            client_factory=lambda **kwargs: object(),
        ) == exit_code


def test_credentials_role_selection_uses_separate_keyring_service(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from safefix.cli import main

    keyring = FakeKeyring()
    credentials = CredentialsResolver(keyring)
    monkeypatch.setattr("getpass.getpass", lambda _: "test-role-key")

    assert main(
        ["credentials", "set", "--role", "test"],
        credentials_factory=lambda: credentials,
    ) == 0

    assert keyring.values == {("safefix-test", "api_key"): "test-role-key"}
    assert capsys.readouterr().out == "credential stored\n"


def test_unscoped_credentials_set_supplies_repair_run_and_repair_role(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from safefix.cli import main

    keyring = FakeKeyring()
    credentials = CredentialsResolver(keyring)
    config = Config(base_url="https://repair.example/v1", model="repair-model")
    client_keys: list[str] = []

    monkeypatch.setattr("getpass.getpass", lambda _: "repair-key")
    assert main(["credentials", "set"], credentials_factory=lambda: credentials) == 0
    assert capsys.readouterr().out == "credential stored\n"
    assert keyring.values == {("safefix", "api_key"): "repair-key"}

    assert main(
        ["credentials", "status", "--role", "repair"],
        credentials_factory=lambda: credentials,
    ) == 0
    assert capsys.readouterr().out == "set\n"

    assert main(
        ["run", str(tmp_path), "--plain"],
        credentials_factory=lambda: credentials,
        config_loader=lambda *_args, **_kwargs: config,
        runner_factory=lambda _root, **_kwargs: FakeRunner(
            SessionResult(stop_reason=StopReason.SUCCESS)
        ),
        client_factory=lambda **kwargs: client_keys.append(kwargs["api_key"]) or object(),
    ) == 0

    assert client_keys == ["repair-key"]


def test_credentials_cli_does_not_accept_raw_api_key_argument() -> None:
    from safefix.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["credentials", "set", "--role", "test", "raw-key"])
