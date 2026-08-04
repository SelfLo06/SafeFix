from pathlib import Path

import pytest

from safefix.approval import ApprovalProvider
from safefix.models import Config, SessionResult, StopReason


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
        ["run", "--project-root", str(tmp_path)],
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
    from safefix.__main__ import main as module_main

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
            "--project-root",
            str(tmp_path),
            "--max-steps",
            "7",
            "--pytest-arg=-q",
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
    assert module_main is main
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


def test_credentials_set_status_clear(capsys: pytest.CaptureFixture[str]) -> None:
    from safefix.cli import main

    credentials = FakeCredentials()

    assert main(["credentials", "status"], credentials_factory=lambda: credentials) == 0
    assert capsys.readouterr().out == "not set\n"
    assert main(["credentials", "set", "stored-test-key"], credentials_factory=lambda: credentials) == 0
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
        ["run", "--project-root", str(tmp_path)],
        credentials_factory=lambda: credentials,
        config_loader=lambda root, overrides, require_llm: config,
        runner_factory=runner_factory,
        client_factory=lambda **kwargs: object(),
    ) == 0
    assert isinstance(seen["approval"], ApprovalProvider)
    assert seen["approval"].approve(object()) is False


def test_exit_code_mapping_for_all_stop_reasons(tmp_path: Path) -> None:
    from safefix.cli import main

    credentials = FakeCredentials()
    credentials.set("stored-test-key")
    config = Config(base_url="https://llm.example/v1", model="repair-model")
    expected = {
        StopReason.SUCCESS: 0,
        StopReason.REQUESTED: 0,
        StopReason.MAX_STEPS: 1,
        StopReason.MAX_ROUNDS: 1,
        StopReason.NO_PROGRESS: 1,
        StopReason.ERROR: 2,
        StopReason.CONFIG_ERROR: 3,
    }

    for reason, exit_code in expected.items():
        assert main(
            ["run", "--project-root", str(tmp_path)],
            credentials_factory=lambda: credentials,
            config_loader=lambda root, overrides, require_llm: config,
            runner_factory=lambda root, **kwargs: FakeRunner(
                SessionResult(stop_reason=reason)
            ),
            client_factory=lambda **kwargs: object(),
        ) == exit_code
