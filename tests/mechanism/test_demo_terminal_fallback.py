import io
import json
import os
from pathlib import Path

from safefix.artifacts import ArtifactWriter
from safefix.cli import main
from safefix.events import SessionEvent
from safefix.models import Config, FailureSet, Phase, SessionResult, StopReason
from safefix.session_state import SessionState
from tests.fixtures.tui.fake_terminal import FakeConsole, FakePromptSession, FakeTickSource


class FakeTerminal(io.StringIO):
    def __init__(self, *, tty: bool) -> None:
        super().__init__()
        self.tty = tty
        self.prompt_calls = 0

    def isatty(self) -> bool:
        return self.tty


class _Credentials:
    def get(self) -> str:
        return "offline-test-key"


def run_terminal_demo(tmp_path: Path, *, terminal: FakeTerminal, no_animation: bool):
    import safefix.cli as cli
    from safefix.operator import OperatorCommandQueue
    from safefix.tui import GuidedRepairConsole

    artifact_path = tmp_path / "safefix-session.json"
    ticks = FakeTickSource([0, 1, 2, 3])
    console = FakeConsole()
    original_stdin, original_stdout = cli.sys.stdin, cli.sys.stdout
    original_term = os.environ.get("TERM")
    original_no_color = os.environ.get("NO_COLOR")
    os.environ["TERM"] = "xterm"
    os.environ.pop("NO_COLOR", None)
    cli.sys.stdin = terminal
    cli.sys.stdout = terminal

    def runner_factory(_root: Path, **kwargs: object):
        sink = kwargs["event_sink"]
        event = SessionEvent(1, "2026-08-07T00:00:00Z", Phase.EVALUATE, "pytest", {
            "summary": "running", "status": "running"
        })
        if hasattr(sink, "emit"):
            sink.emit(event)
        else:
            sink("Status: evaluate")
        state = SessionState(FailureSet(frozenset()))
        result = ArtifactWriter(artifact_path).write(
            state, SessionResult(StopReason.SUCCESS)
        )
        return type("Runner", (), {"run": lambda _self: result})()

    def tui_factory(command_queue, controller_factory, capabilities, _no_animation):
        return GuidedRepairConsole(
            command_queue, controller_factory, FakePromptSession, console,
            capabilities, ticks,
        )

    try:
        exit_code = main(
            ["run", str(tmp_path), "--no-animation" if no_animation else "--tui"],
            credentials_factory=_Credentials,
            config_loader=lambda *_args, **_kwargs: Config(
                base_url="https://repair.invalid/v1", model="repair-model"
            ),
            runner_factory=runner_factory,
            client_factory=lambda **_kwargs: object(),
            tty_detector=lambda stream: stream.isatty(),
            tui_factory=tui_factory,
        )
    finally:
        cli.sys.stdin, cli.sys.stdout = original_stdin, original_stdout
        if original_term is None:
            os.environ.pop("TERM", None)
        else:
            os.environ["TERM"] = original_term
        if original_no_color is None:
            os.environ.pop("NO_COLOR", None)
        else:
            os.environ["NO_COLOR"] = original_no_color

    artifact_text = artifact_path.read_text(encoding="utf-8")
    return type("TerminalDemoResult", (), {
        "tui_started": terminal.tty,
        "transcript_is_plain": not terminal.tty,
        "stop_reason": StopReason.SUCCESS,
        "exit_code": exit_code,
        "plain_output": terminal.getvalue(),
        "artifact_text": artifact_text,
        "artifact": json.loads(artifact_text),
        "animation_ticks": tuple(ticks.ticks),
        "rendered_lines": tuple(str(line[0]) for line in console.lines),
    })()


def run_animated_tui_demo(tmp_path: Path):
    return run_terminal_demo(tmp_path, terminal=FakeTerminal(tty=True), no_animation=False)


def test_terminal_fallback_preserves_plain_harness_outcome(tmp_path: Path) -> None:
    result = run_terminal_demo(tmp_path, terminal=FakeTerminal(tty=False), no_animation=False)
    assert result.tui_started is False
    assert result.transcript_is_plain is True
    assert result.stop_reason is StopReason.SUCCESS
    assert result.exit_code == 0
    assert "SafeFix stopped: success" in result.plain_output
    assert "ANSI" not in result.artifact_text


def test_tty_demo_consumes_fake_input_and_ticks_through_cli_adapter(tmp_path: Path) -> None:
    result = run_terminal_demo(tmp_path, terminal=FakeTerminal(tty=True), no_animation=False)
    assert result.tui_started is True
    assert result.animation_ticks == (0, 1, 2)
    assert "Status: evaluate" in result.rendered_lines


def test_no_animation_still_routes_tty_through_adapter_without_ticks(tmp_path: Path) -> None:
    result = run_terminal_demo(tmp_path, terminal=FakeTerminal(tty=True), no_animation=True)
    assert result.tui_started is True
    assert result.animation_ticks == ()


def test_artifact_contains_semantics_not_presentation_frames(tmp_path: Path) -> None:
    result = run_animated_tui_demo(tmp_path)
    assert result.artifact["stop_reason"] == "success"
    assert "spinner" not in repr(result.artifact)
    assert "ANSI" not in repr(result.artifact)
