from pathlib import Path

import pytest

from safefix.approval import ApprovalProvider
from safefix.models import BaselineSource, Config, SessionResult, StopReason
from safefix.credentials import CredentialsResolver
from safefix.models import ModelRole


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


def test_runtime_preflight_overrides_reload_the_cached_config(tmp_path: Path) -> None:
    from safefix.cli import main

    credentials = FakeCredentials()
    credentials.set("stored-test-key")
    loaded_overrides: list[dict[str, object]] = []

    def config_loader(
        _root: Path, overrides: dict[str, object], require_llm: bool
    ) -> Config:
        del require_llm
        loaded_overrides.append(dict(overrides))
        return Config(
            base_url="https://llm.example/v1",
            model="repair-model",
            baseline_source=BaselineSource(overrides.get("baseline_source", "existing")),
            generate_tests=bool(overrides.get("generate_tests", False)),
        )

    def runner_factory(project_root: Path, **kwargs: object) -> FakeRunner:
        runtime_overrides = kwargs["cli_overrides"]
        assert isinstance(runtime_overrides, dict)
        runtime_overrides.update(
            {"baseline_source": "generated", "generate_tests": True}
        )
        loader = kwargs["config_loader"]
        assert callable(loader)
        refreshed = loader(project_root, runtime_overrides, require_llm=True)
        assert refreshed.baseline_source.value == "generated"
        assert refreshed.generate_tests is True
        return FakeRunner(SessionResult(stop_reason=StopReason.SUCCESS))

    assert main(
        ["run", str(tmp_path)],
        credentials_factory=lambda: credentials,
        config_loader=config_loader,
        runner_factory=runner_factory,
        client_factory=lambda **_kwargs: object(),
    ) == 0

    assert loaded_overrides[-1] == {
        "baseline_source": "generated",
        "generate_tests": True,
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


def test_credentials_commands_explain_environment_storage(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from safefix.cli import main

    assert main(["credentials", "status"]) == 0
    output = capsys.readouterr().out
    assert "不会存储凭据" in output
    assert "SAFEFIX_TEST_API_KEY" in output
    assert "SAFEFIX_REPAIR_API_KEY" in output
    assert "SAFEFIX_REVIEW_API_KEY" in output
    assert main(["credentials", "set", "--role", "test"]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "SAFEFIX_TEST_API_KEY"
    assert main(["credentials", "clear", "--role", "review"]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "SAFEFIX_REVIEW_API_KEY"


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


def test_credentials_cli_does_not_accept_raw_api_key_argument() -> None:
    from safefix.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["credentials", "set", "--role", "test", "raw-key"])


def test_no_arguments_launches_tty_wizard_and_bootstraps_repair_config(
    tmp_path: Path,
) -> None:
    from safefix.cli import main

    answers = iter(["", "", "repair-model"])
    config = Config(base_url="https://llm.example/v1", model="repair-model")
    seen: dict[str, object] = {}
    credentials = FakeCredentials()
    credentials.set("demo-key")

    def runner_factory(project_root: Path, **kwargs: object) -> FakeRunner:
        seen["project_root"] = project_root
        return FakeRunner(SessionResult(stop_reason=StopReason.SUCCESS))

    class WizardTui:
        def __init__(self, _queue: object, controller_factory: object, *_args: object) -> None:
            self._controller_factory = controller_factory

        def run(self) -> SessionResult:
            return self._controller_factory(object()).run()

    assert main(
        [],
        credentials_factory=lambda: credentials,
        config_loader=lambda *_args, **_kwargs: config,
        runner_factory=runner_factory,
        client_factory=lambda **_kwargs: object(),
        tty_detector=lambda _stream: True,
        tui_factory=WizardTui,
        input_fn=lambda _prompt: next(answers),
        output_fn=lambda line: seen.setdefault("output", []).append(line),
        cwd=tmp_path,
    ) == 0

    assert seen["project_root"] == tmp_path.resolve()
    assert (tmp_path / "safefix.toml").read_text() == (
        'base_url = "https://api.openai.com/v1"\n'
        'model = "repair-model"\n'
    )


def test_no_arguments_fail_closed_without_tty() -> None:
    from safefix.cli import main

    assert main([], tty_detector=lambda _stream: False) == 2


def test_wizard_requires_a_model_when_config_is_missing(tmp_path: Path) -> None:
    from safefix.cli import main

    answers = iter(["", "https://llm.example/v1", "", "repair-model"])
    messages: list[str] = []
    config = Config(base_url="https://llm.example/v1", model="repair-model")
    credentials = FakeCredentials()
    credentials.set("demo-key")

    assert main(
        [],
        credentials_factory=lambda: credentials,
        config_loader=lambda *_args, **_kwargs: config,
        runner_factory=lambda _root, **_kwargs: FakeRunner(
            SessionResult(stop_reason=StopReason.SUCCESS)
        ),
        client_factory=lambda **_kwargs: object(),
        tty_detector=lambda _stream: True,
        tui_factory=lambda *_args: FakeRunner(
            SessionResult(stop_reason=StopReason.SUCCESS)
        ),
        input_fn=lambda _prompt: next(answers),
        output_fn=messages.append,
        cwd=tmp_path,
    ) == 0

    assert any("不能为空" in message for message in messages)
