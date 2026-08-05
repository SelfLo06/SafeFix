"""Command-line entrypoints for SafeFix."""

import argparse
import getpass
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from .approval import ApprovalProvider
from .config import ConfigError, load_config
from .credentials import CredentialError, CredentialsResolver
from .llm.openai_compatible import OpenAICompatibleClient
from .llm.base import LLMResponseError
from .models import Config, StopReason, exit_code_for_stop_reason
from .runner import SessionRunner


EXIT_CODES = {reason: exit_code_for_stop_reason(reason) for reason in StopReason}


class UrllibHTTPTransport:
    """Production HTTP transport for OpenAI-compatible completion requests."""

    def post(
        self, url: str, headers: dict[str, str], json_body: dict[str, Any], timeout: float
    ) -> dict[str, Any]:
        request = Request(
            url,
            data=json.dumps(json_body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except ValueError as exc:
            raise LLMResponseError("invalid JSON response") from exc


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
    run.add_argument("--use-memory", action="store_true")
    run.add_argument("--non-interactive", action="store_true")

    credentials = commands.add_parser("credentials")
    credential_commands = credentials.add_subparsers(dest="credentials_command", required=True)
    credential_set = credential_commands.add_parser("set")
    credential_commands.add_parser("status")
    credential_commands.add_parser("clear")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    credentials_factory: Callable[[], CredentialsResolver] = CredentialsResolver,
    config_loader: Callable[..., Config] = load_config,
    runner_factory: Callable[..., SessionRunner] = SessionRunner,
    client_factory: Callable[..., OpenAICompatibleClient] = production_client,
    approval_factory: Callable[[], ApprovalProvider] = ApprovalProvider,
) -> int:
    args = build_parser().parse_args(argv)
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
    )


def _credentials_command(args: argparse.Namespace, credentials: CredentialsResolver) -> int:
    if args.credentials_command == "set":
        try:
            value = getpass.getpass("API key: ")
        except (EOFError, OSError) as exc:
            raise CredentialError("credential prompt failed") from exc
        credentials.set(value)
        print("credential stored")
    elif args.credentials_command == "status":
        print("set" if credentials.status() else "not set")
    else:
        credentials.clear()
        print("credential cleared")
    return 0


def _run_command(
    args: argparse.Namespace,
    *,
    credentials: CredentialsResolver,
    config_loader: Callable[..., Config],
    runner_factory: Callable[..., SessionRunner],
    client_factory: Callable[..., OpenAICompatibleClient],
    approval_factory: Callable[[], ApprovalProvider],
) -> int:
    project_root = args.project_path.resolve()
    overrides = _overrides(args)
    try:
        config = config_loader(project_root, overrides, require_llm=True)
        api_key = credentials.get()
        client = client_factory(
            base_url=config.base_url,
            model=config.model,
            api_key=api_key,
        )
    except (ConfigError, CredentialError) as exc:
        exit_code = EXIT_CODES[StopReason.CONFIG_ERROR]
        print(f"SafeFix configuration error: {exc} (exit code {exit_code})")
        return exit_code

    def cached_config_loader(
        _project_root: Path, _cli_overrides: dict, *, require_llm: bool = False
    ) -> Config:
        return config

    result = runner_factory(
        project_root,
        cli_overrides=overrides,
        credentials=_CachedCredentials(api_key),
        config_loader=cached_config_loader,
        llm_client=client,
        event_sink=print,
        use_memory=args.use_memory,
        approval=(
            ApprovalProvider(interactive=False)
            if args.non_interactive
            else approval_factory()
        ),
    ).run()
    exit_code = EXIT_CODES[result.stop_reason]
    print(f"SafeFix stopped: {result.stop_reason.value} (exit code {exit_code})")
    return exit_code


def _overrides(args: argparse.Namespace) -> dict[str, object]:
    return {
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
        }.items()
        if value is not None
    }
