"""Command-line entrypoints for SafeFix."""

import argparse
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .approval import ApprovalProvider
from .config import ConfigError, load_config
from .credentials import CredentialError, CredentialsResolver
from .llm.openai_compatible import OpenAICompatibleClient
from .llm.roles import UrllibHTTPTransport
from .models import (
    AcceptanceMode,
    Config,
    BaselineSource,
    HighRiskConfirmation,
    ModelRole,
    ROLE_API_KEY_ENV,
    StopReason,
    exit_code_for_stop_reason,
)
from .review import ReviewModelClient
from .runner import SessionRunner


EXIT_CODES = {reason: exit_code_for_stop_reason(reason) for reason in StopReason}


class _Tui(Protocol):
    def run(self):
        """Run the presentation and return the session result."""


TuiFactory = Callable[[object, Callable[..., SessionRunner], object, bool], _Tui]


class _CachedCredentials:
    """Provide SessionRunner the credential already validated by the CLI."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def get(self) -> str:
        return self._api_key


def production_client(*, base_url: str, model: str, api_key: str) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        base_url=base_url,
        model=model,
        api_key=api_key,
        transport=UrllibHTTPTransport(),
    )


def production_tui(
    command_queue: object,
    controller_factory: Callable[..., SessionRunner],
    capabilities: object,
    no_animation: bool,
) -> _Tui:
    """Construct the optional terminal adapter only after TUI selection."""
    from prompt_toolkit import PromptSession
    from rich.console import Console

    from .tui import GuidedRepairConsole

    class TickSource:
        def __init__(self) -> None:
            self._tick = 0

        def next_tick(self) -> int:
            value = self._tick
            self._tick += 1
            return value

    return GuidedRepairConsole(
        command_queue,
        controller_factory,
        PromptSession,
        Console(),
        capabilities,
        TickSource(),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="safefix")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run")
    run.add_argument("project_path", type=Path)
    run.add_argument("--max-steps", type=int)
    run.add_argument("--max-rounds", type=int)
    run.add_argument("--max-no-progress-rounds", type=int)
    run.add_argument("--allowed-path", action="append")
    run.add_argument("--excluded-path", action="append")
    run.add_argument("--pytest-args", action="append")
    run.add_argument("--base-url")
    run.add_argument("--model")
    run.add_argument("--generate-tests", action="store_true")
    run.add_argument("--baseline-source", choices=["existing", "generated", "mixed"])
    run.add_argument("--acceptance-mode", choices=["review", "standard", "high-risk"])
    run.add_argument("--stability-runs", type=int)
    run.add_argument("--max-auto-accepted-failures", type=int)
    run.add_argument("--test-base-url")
    run.add_argument("--test-model")
    run.add_argument("--review-base-url")
    run.add_argument("--review-model")
    run.add_argument("--use-memory", action="store_true")
    run.add_argument("--non-interactive", action="store_true")
    presentation = run.add_mutually_exclusive_group()
    presentation.add_argument("--tui", action="store_true")
    presentation.add_argument("--plain", action="store_true")
    run.add_argument("--no-animation", action="store_true")

    credentials = commands.add_parser("credentials")
    credential_commands = credentials.add_subparsers(dest="credentials_command", required=True)
    credential_set = credential_commands.add_parser("set")
    role_values = [role.value for role in ModelRole]
    credential_set.add_argument("--role", choices=role_values)
    credential_status = credential_commands.add_parser("status")
    credential_status.add_argument("--role", choices=role_values)
    credential_clear = credential_commands.add_parser("clear")
    credential_clear.add_argument("--role", choices=role_values)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    credentials_factory: Callable[[], CredentialsResolver] = CredentialsResolver,
    config_loader: Callable[..., Config] = load_config,
    runner_factory: Callable[..., SessionRunner] = SessionRunner,
    client_factory: Callable[..., OpenAICompatibleClient] = production_client,
    approval_factory: Callable[[], ApprovalProvider] = ApprovalProvider,
    tty_detector: Callable[[object], bool] = lambda stream: bool(stream.isatty()),
    tui_factory: TuiFactory = production_tui,
    input_fn: Callable[[str], str] | None = None,
    output_fn: Callable[[str], None] | None = None,
    cwd: Path | None = None,
) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    capable_tty = tty_detector(sys.stdin) and tty_detector(sys.stdout)
    if not raw_argv:
        return _launch_wizard(
            capable_tty=capable_tty,
            credentials_factory=credentials_factory,
            config_loader=config_loader,
            runner_factory=runner_factory,
            client_factory=client_factory,
            approval_factory=approval_factory,
            tty_detector=tty_detector,
            tui_factory=tui_factory,
            input_fn=input_fn,
            output_fn=output_fn,
            cwd=cwd,
        )

    args = build_parser().parse_args(raw_argv)
    credentials = credentials_factory()
    if args.command == "credentials":
        try:
            return _credentials_command(args, credentials)
        except CredentialError as exc:
            exit_code = EXIT_CODES[StopReason.CONFIG_ERROR]
            print(f"SafeFix credential error: {exc} (exit code {exit_code})")
            return exit_code
    return _run_command(
        args,
        credentials=credentials,
        config_loader=config_loader,
        runner_factory=runner_factory,
        client_factory=client_factory,
        approval_factory=approval_factory,
        tty_detector=tty_detector,
        tui_factory=tui_factory,
    )


def _credentials_command(args: argparse.Namespace, credentials: CredentialsResolver) -> int:
    if args.role is not None:
        names = (ROLE_API_KEY_ENV[ModelRole(args.role)],)
    else:
        names = tuple(ROLE_API_KEY_ENV[role] for role in ModelRole)
    print("SafeFix reads API credentials from environment variables and does not store them:")
    for name in names:
        print(name)
    return 0


def _run_command(
    args: argparse.Namespace,
    *,
    credentials: CredentialsResolver,
    config_loader: Callable[..., Config],
    runner_factory: Callable[..., SessionRunner],
    client_factory: Callable[..., OpenAICompatibleClient],
    approval_factory: Callable[[], ApprovalProvider],
    tty_detector: Callable[[object], bool],
    tui_factory: TuiFactory,
) -> int:
    project_root = args.project_path.resolve()
    overrides = _overrides(args)
    capable_tty = tty_detector(sys.stdin) and tty_detector(sys.stdout)
    try:
        config = config_loader(project_root, overrides, require_llm=True)
        resolved_high_risk = (
            AcceptanceMode(config.acceptance_mode) is AcceptanceMode.HIGH_RISK
        )
        explicit_high_risk = args.acceptance_mode == AcceptanceMode.HIGH_RISK.value
        if resolved_high_risk and not explicit_high_risk:
            raise ConfigError("high-risk acceptance requires explicit CLI opt-in")
        if explicit_high_risk and (args.non_interactive or not capable_tty):
            raise ConfigError(
                "high-risk acceptance requires a capable interactive TTY"
            )
        if resolved_high_risk and (
            not config.review_base_url.strip() or not config.review_model.strip()
        ):
            raise ConfigError(
                "high-risk acceptance requires Review Model configuration"
            )

        api_key = credentials.for_role(ModelRole.REPAIR).get()
        client = client_factory(
            base_url=config.base_url,
            model=config.model,
            api_key=api_key,
        )
        test_client = None
        if config.generate_tests and BaselineSource(config.baseline_source) in {
            BaselineSource.GENERATED,
            BaselineSource.MIXED,
        }:
            test_client = client_factory(
                base_url=config.test_base_url,
                model=config.test_model,
                api_key=credentials.for_role(ModelRole.TEST).get(),
            )
        review_client = None
        final_review_client = None
        if config.review_base_url.strip() and config.review_model.strip():
            review_client = ReviewModelClient(
                client_factory(
                    base_url=config.review_base_url,
                    model=config.review_model,
                    api_key=credentials.for_role(ModelRole.REVIEW).get(),
                )
            )
            final_review_client = review_client
    except (ConfigError, CredentialError) as exc:
        exit_code = EXIT_CODES[StopReason.CONFIG_ERROR]
        print(f"SafeFix configuration error: {exc} (exit code {exit_code})")
        return exit_code

    def cached_config_loader(
        _project_root: Path, _cli_overrides: dict, *, require_llm: bool = False
    ) -> Config:
        return config

    approval = (
        ApprovalProvider(interactive=False)
        if args.non_interactive or not capable_tty
        else approval_factory()
    )
    high_risk_confirmation = None
    if args.acceptance_mode == AcceptanceMode.HIGH_RISK.value:
        if not approval.approve(
            HighRiskConfirmation(
                confirmed=True,
                source="cli",
                summary="explicit --acceptance-mode high-risk confirmation",
            )
        ):
            exit_code = EXIT_CODES[StopReason.CONFIG_ERROR]
            print(
                "SafeFix configuration error: high-risk acceptance requires interactive confirmation "
                f"(exit code {exit_code})"
            )
            return exit_code
        high_risk_confirmation = True

    def make_runner(event_sink: object, command_queue: object | None = None) -> SessionRunner:
        return runner_factory(
            project_root,
            cli_overrides=overrides,
            credentials=_CachedCredentials(api_key),
            config_loader=cached_config_loader,
            llm_client=client,
            test_client=test_client,
            review_client=review_client,
            final_review_client=final_review_client,
            event_sink=event_sink,
            operator_queue=command_queue,
            use_memory=args.use_memory,
            approval=approval,
            high_risk_confirmation=high_risk_confirmation,
        )

    use_tui = not args.non_interactive and not args.plain and capable_tty
    if use_tui:
        from .operator import OperatorCommandQueue
        from .tui import TerminalCapabilities

        dumb_terminal = os.environ.get("TERM") == "dumb"
        color = not dumb_terminal and "NO_COLOR" not in os.environ
        capabilities = TerminalCapabilities(
            interactive=True,
            color=color,
            unicode=not dumb_terminal,
            animation=color and not dumb_terminal and not args.no_animation,
        )
        command_queue = OperatorCommandQueue()
        result = tui_factory(command_queue, make_runner, capabilities, args.no_animation).run()
    else:
        result = make_runner(print).run()
    exit_code = EXIT_CODES[result.stop_reason]
    print(f"SafeFix stopped: {result.stop_reason.value} (exit code {exit_code})")
    return exit_code


def _launch_wizard(
    *,
    capable_tty: bool,
    credentials_factory: Callable[[], CredentialsResolver],
    config_loader: Callable[..., Config],
    runner_factory: Callable[..., SessionRunner],
    client_factory: Callable[..., OpenAICompatibleClient],
    approval_factory: Callable[[], ApprovalProvider],
    tty_detector: Callable[[object], bool],
    tui_factory: TuiFactory,
    input_fn: Callable[[str], str] | None,
    output_fn: Callable[[str], None] | None,
    cwd: Path | None,
) -> int:
    write = print if output_fn is None else output_fn
    if not capable_tty:
        write("SafeFix: no-argument startup requires an interactive TTY; use 'safefix run PATH'.")
        return EXIT_CODES[StopReason.CONFIG_ERROR]

    ask = input if input_fn is None else input_fn
    default_project = (Path.cwd() if cwd is None else cwd).resolve()
    project_text = ask(f"Project [{default_project}] > ").strip()
    project_root = Path(project_text or default_project).expanduser().resolve()
    if not project_root.is_dir():
        write(f"SafeFix configuration error: project directory does not exist: {project_root}")
        return EXIT_CODES[StopReason.CONFIG_ERROR]

    config_path = project_root / "safefix.toml"
    has_tests = (project_root / "tests").is_dir()
    write("SafeFix v0.2")
    write(f"Project: {project_root}")
    write("Mode: standard")
    write(f"Tests: {'existing' if has_tests else 'not detected'}")
    write("UI: TUI")

    if not config_path.exists():
        write("No safefix.toml found.")
        base_url = ask("API base URL [https://api.openai.com/v1] > ").strip()
        base_url = base_url or "https://api.openai.com/v1"
        model = ""
        while not model:
            model = ask("Model > ").strip()
            if not model:
                write("Model is required.")
        config_path.write_text(
            f"base_url = {json.dumps(base_url)}\nmodel = {json.dumps(model)}\n",
            encoding="utf-8",
        )
        write(f"Created {config_path}")

    write("Starting...")
    return main(
        ["run", str(project_root), "--tui"],
        credentials_factory=credentials_factory,
        config_loader=config_loader,
        runner_factory=runner_factory,
        client_factory=client_factory,
        approval_factory=approval_factory,
        tty_detector=tty_detector,
        tui_factory=tui_factory,
        input_fn=input_fn,
        output_fn=output_fn,
        cwd=cwd,
    )


def _overrides(args: argparse.Namespace) -> dict[str, object]:
    values = {
        key: value
        for key, value in {
            "max_steps": args.max_steps,
            "max_rounds": args.max_rounds,
            "max_no_progress_rounds": args.max_no_progress_rounds,
            "allowed_paths": args.allowed_path,
            "excluded_paths": args.excluded_path,
            "pytest_args": args.pytest_args,
            "base_url": args.base_url,
            "model": args.model,
            "baseline_source": args.baseline_source,
            "acceptance_mode": args.acceptance_mode,
            "stability_runs": args.stability_runs,
            "max_auto_accepted_failures": args.max_auto_accepted_failures,
            "test_base_url": args.test_base_url,
            "test_model": args.test_model,
            "review_base_url": args.review_base_url,
            "review_model": args.review_model,
        }.items()
        if value is not None
    }
    if args.generate_tests:
        values["generate_tests"] = True
    return values
