from pathlib import Path

import pytest

from safefix.approval import ApprovalProvider
from safefix.credentials import CredentialsResolver
from safefix.models import Config, ModelRole, SessionResult, StopReason


class FakeCredentials:
    def get(self) -> str:
        return "stored-test-key"

    def for_role(self, _role: ModelRole) -> "FakeCredentials":
        return self


class FakeRunner:
    def run(self) -> SessionResult:
        return SessionResult(stop_reason=StopReason.SUCCESS)


class CapturingRunnerFactory:
    plain_event_sink_used = False
    received_overrides: dict[str, object] = {}
    operator_queue = None

    @classmethod
    def reset(cls) -> None:
        cls.plain_event_sink_used = False
        cls.received_overrides = {}
        cls.operator_queue = None

    def __call__(self, _project_root: Path, **kwargs: object) -> FakeRunner:
        type(self).plain_event_sink_used = kwargs["event_sink"] is print
        type(self).received_overrides = kwargs["cli_overrides"]
        type(self).operator_queue = kwargs.get("operator_queue")
        return FakeRunner()


class CapturingTui:
    created = False
    no_animation = False
    command_queue = None

    def __init__(self, command_queue, controller_factory, capabilities, no_animation):
        type(self).created = True
        type(self).no_animation = no_animation
        type(self).command_queue = command_queue
        self._controller_factory = controller_factory
        self._command_queue = command_queue
        self.capabilities = capabilities

    def run(self) -> SessionResult:
        return self._controller_factory(object(), self._command_queue).run()


class FailIfCalledTui:
    created = False

    def __init__(self, *_args: object) -> None:
        type(self).created = True
        raise AssertionError("plain mode must not construct the TUI")


def _main(
    tmp_path: Path,
    *flags: str,
    tty: bool,
    tui_factory=FailIfCalledTui,
) -> int:
    from safefix.cli import main

    CapturingRunnerFactory.reset()
    FailIfCalledTui.created = False
    CapturingTui.created = False
    CapturingTui.command_queue = None
    return main(
        ["run", str(tmp_path), *flags],
        tty_detector=lambda _stream: tty,
        tui_factory=tui_factory,
        runner_factory=CapturingRunnerFactory(),
        credentials_factory=FakeCredentials,
        config_loader=lambda *_args, **_kwargs: Config(
            base_url="https://llm.example/v1", model="repair-model"
        ),
        client_factory=lambda **_kwargs: object(),
    )


def test_run_parser_has_mutually_exclusive_presentation_flags() -> None:
    from safefix.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", ".", "--tui", "--plain"])


def test_run_parser_accepts_v2_config_overrides() -> None:
    from safefix.cli import build_parser

    args = build_parser().parse_args(
        [
            "run",
            ".",
            "--generate-tests",
            "--baseline-source",
            "generated",
            "--acceptance-mode",
            "high-risk",
            "--stability-runs",
            "2",
            "--max-auto-accepted-failures",
            "1",
            "--test-base-url",
            "https://test.example/v1",
            "--test-model",
            "test-model",
            "--review-base-url",
            "https://review.example/v1",
            "--review-model",
            "review-model",
        ]
    )

    assert args.generate_tests is True
    assert args.baseline_source == "generated"
    assert args.acceptance_mode == "high-risk"
    assert args.stability_runs == 2
    assert args.max_auto_accepted_failures == 1
    assert args.test_model == "test-model"
    assert args.review_model == "review-model"


def test_tty_defaults_to_injected_tui_with_console_runner_wiring(tmp_path: Path) -> None:
    assert _main(tmp_path, tty=True, tui_factory=CapturingTui) == 0

    assert CapturingTui.created is True
    assert CapturingRunnerFactory.plain_event_sink_used is False
    assert CapturingRunnerFactory.operator_queue is CapturingTui.command_queue


def test_plain_forces_legacy_event_sink(tmp_path: Path) -> None:
    assert _main(tmp_path, "--plain", tty=True) == 0

    assert CapturingRunnerFactory.plain_event_sink_used is True
    assert FailIfCalledTui.created is False


def test_non_tty_never_starts_prompt_toolkit_even_with_tui_flag(tmp_path: Path) -> None:
    assert _main(tmp_path, "--tui", "--no-animation", tty=False) == 0

    assert FailIfCalledTui.created is False
    assert CapturingRunnerFactory.plain_event_sink_used is True


def test_no_animation_is_presentation_only(tmp_path: Path) -> None:
    assert _main(tmp_path, "--tui", "--no-animation", tty=True, tui_factory=CapturingTui) == 0

    assert CapturingTui.created is True
    assert CapturingTui.no_animation is True
    assert CapturingRunnerFactory.received_overrides == {}
    assert CapturingRunnerFactory.operator_queue is not None


def test_non_tty_run_uses_fail_closed_approval_for_high_risk_work(tmp_path: Path) -> None:
    from safefix.cli import main

    seen: dict[str, object] = {}

    def runner_factory(_project_root: Path, **kwargs: object) -> FakeRunner:
        seen.update(kwargs)
        return FakeRunner()

    def approval_factory() -> object:
        raise AssertionError("non-TTY runs must not use interactive approval")

    assert main(
        ["run", str(tmp_path), "--acceptance-mode", "high-risk"],
        tty_detector=lambda _stream: False,
        tui_factory=FailIfCalledTui,
        runner_factory=runner_factory,
        credentials_factory=FakeCredentials,
        config_loader=lambda *_args, **_kwargs: Config(
            base_url="https://llm.example/v1", model="repair-model"
        ),
        client_factory=lambda **_kwargs: object(),
        approval_factory=approval_factory,
    ) == 2

    assert seen == {}


def test_cli_wires_configured_role_clients_and_review_adapter(tmp_path: Path) -> None:
    from safefix.cli import main
    from safefix.review import ReviewModelClient

    class Keyring:
        values = {
            ("safefix", "api_key"): "repair-secret",
            ("safefix-test", "api_key"): "test-secret",
            ("safefix-review", "api_key"): "review-secret",
        }

        def get_password(self, service: str, username: str) -> str | None:
            return self.values.get((service, username))

    credentials = CredentialsResolver(Keyring())
    config = Config(
        base_url="https://repair.example/v1",
        model="repair-model",
        generate_tests=True,
        baseline_source="generated",
        test_base_url="https://test.example/v1",
        test_model="test-model",
        review_base_url="https://review.example/v1",
        review_model="review-model",
    )
    clients: list[dict[str, str]] = []
    seen: dict[str, object] = {}

    def client_factory(**kwargs: str) -> object:
        clients.append(kwargs)
        return kwargs

    def runner_factory(_root: Path, **kwargs: object) -> FakeRunner:
        seen.update(kwargs)
        return FakeRunner()

    assert main(
        ["run", str(tmp_path), "--plain"],
        credentials_factory=lambda: credentials,
        config_loader=lambda *_args, **_kwargs: config,
        runner_factory=runner_factory,
        client_factory=client_factory,
    ) == 0

    assert [(item["base_url"], item["model"], item["api_key"]) for item in clients] == [
        ("https://repair.example/v1", "repair-model", "repair-secret"),
        ("https://test.example/v1", "test-model", "test-secret"),
        ("https://review.example/v1", "review-model", "review-secret"),
    ]
    assert seen["llm_client"] is clients[0]
    assert seen["test_client"] is clients[1]
    assert isinstance(seen["review_client"], ReviewModelClient)
    assert isinstance(seen["final_review_client"], ReviewModelClient)


def test_toml_high_risk_requires_explicit_cli_opt_in_before_runner(
    tmp_path: Path,
) -> None:
    from safefix.cli import main

    (tmp_path / "safefix.toml").write_text(
        'base_url = "https://repair.example/v1"\n'
        'model = "repair-model"\n'
        'acceptance_mode = "high-risk"\n',
        encoding="utf-8",
    )
    runner_created = False

    def runner_factory(_root: Path, **_kwargs: object) -> FakeRunner:
        nonlocal runner_created
        runner_created = True
        return FakeRunner()

    assert main(
        ["run", str(tmp_path), "--plain"],
        credentials_factory=FakeCredentials,
        runner_factory=runner_factory,
        client_factory=lambda **_kwargs: object(),
        tty_detector=lambda _stream: True,
    ) == 2
    assert runner_created is False


def test_cli_requires_high_risk_confirmation_and_passes_record_to_runner(
    tmp_path: Path,
) -> None:
    from safefix.cli import main

    config = Config(
        base_url="https://repair.example/v1",
        model="repair-model",
        acceptance_mode="high-risk",
    )
    seen: dict[str, object] = {}

    def runner_factory(_root: Path, **kwargs: object) -> FakeRunner:
        seen.update(kwargs)
        return FakeRunner()

    approval = ApprovalProvider(interactive=True, input_fn=lambda _prompt: "yes")
    assert main(
        ["run", str(tmp_path), "--plain", "--acceptance-mode", "high-risk"],
        credentials_factory=FakeCredentials,
        config_loader=lambda *_args, **_kwargs: config,
        runner_factory=runner_factory,
        client_factory=lambda **kwargs: object(),
        approval_factory=lambda: approval,
        tty_detector=lambda _stream: True,
    ) == 0

    assert seen["high_risk_confirmation"] is True
    assert seen["approval"] is approval
