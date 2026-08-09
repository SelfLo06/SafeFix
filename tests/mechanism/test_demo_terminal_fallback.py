import io
import json
import os
from pathlib import Path
from unicodedata import category

from safefix.artifacts import ArtifactWriter
from safefix.cli import main
from safefix.events import SessionEvent
from safefix.models import Config, FailureSet, ModelRole, Phase, SessionResult, StopReason
from safefix.session_state import SessionState
from tests.fixtures.tui.fake_terminal import FakeConsole, FakeTickSource


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

    def for_role(self, _role: ModelRole) -> "_Credentials":
        return self


class StatefulArtifactRunner:
    def __init__(
        self,
        artifact_path: Path,
        command_queue: object | None,
        consumed_guidance: list[tuple[str, ...]],
        session_results: list[SessionResult],
    ) -> None:
        self._artifact_path = artifact_path
        self._command_queue = command_queue
        self._consumed_guidance = consumed_guidance
        self._session_results = session_results
        self.state = SessionState(
            FailureSet(frozenset({"tests/test_semantic.py::test_semantic_artifact"}))
        )

    def run(self) -> SessionResult:
        if self._command_queue is not None:
            return SessionResult(StopReason.SUCCESS)
        return self.finalize(SessionResult(StopReason.SUCCESS))

    def finalize(self, result: SessionResult) -> SessionResult:
        if self._command_queue is not None:
            self._consumed_guidance.append(
                self._command_queue.drain_ready_guidance()
            )
        self.state.increment_step()
        written = ArtifactWriter(self._artifact_path).write(self.state, result)
        self._session_results.append(written)
        return written


def _artifact_strings(value: object) -> tuple[str, ...]:
    if isinstance(value, dict):
        return tuple(value) + tuple(
            item for nested in value.values() for item in _artifact_strings(nested)
        )
    if isinstance(value, list):
        return tuple(item for nested in value for item in _artifact_strings(nested))
    if isinstance(value, str):
        return (value,)
    return ()


def _contains_control(text: str) -> bool:
    return any(category(character) == "Cc" for character in text)


def run_terminal_demo(tmp_path: Path, *, terminal: FakeTerminal, no_animation: bool):
    import safefix.cli as cli
    from safefix.operator import OperatorCommandQueue
    from safefix.tui import GuidedRepairConsole

    artifact_path = tmp_path / "safefix-session.json"
    ticks = FakeTickSource([0, 1, 2, 3])
    console = FakeConsole()
    tui_factory_calls: list[OperatorCommandQueue] = []
    plain_events: list[SessionEvent] = []
    session_results: list[SessionResult] = []
    consumed_guidance: list[tuple[str, ...]] = []
    runners: list[StatefulArtifactRunner] = []
    original_stdin, original_stdout = cli.sys.stdin, cli.sys.stdout
    original_term = os.environ.get("TERM")
    original_no_color = os.environ.get("NO_COLOR")
    os.environ["TERM"] = "xterm"
    os.environ.pop("NO_COLOR", None)
    cli.sys.stdin = terminal
    cli.sys.stdout = terminal

    def runner_factory(_root: Path, **kwargs: object):
        sink = kwargs["event_sink"]

        def emit_event() -> None:
            event = SessionEvent(
                1,
                "2026-08-07T00:00:00Z",
                Phase.EVALUATE,
                "pytest",
                {"summary": "running", "status": "running"},
            )
            if hasattr(sink, "emit"):
                sink.emit(event)
            else:
                plain_events.append(event)
                sink("Status: evaluate")

        runner = StatefulArtifactRunner(
            artifact_path,
            kwargs["operator_queue"],
            consumed_guidance,
            session_results,
        )
        runners.append(runner)
        emit_event()
        return runner

    class PromptSession:
        async def prompt_async(self, _prompt: str) -> str:
            if terminal.prompt_calls == 2:
                raise EOFError
            terminal.prompt_calls += 1
            return "/guide" if terminal.prompt_calls == 1 else "preserve the public API"

    def tui_factory(command_queue, controller_factory, capabilities, _no_animation):
        class ArtifactGuidedRepairConsole(GuidedRepairConsole):
            def run(self) -> SessionResult:
                result = super().run()
                return runners[-1].finalize(result)

        tui_factory_calls.append(command_queue)
        return ArtifactGuidedRepairConsole(
            command_queue, controller_factory, PromptSession, console,
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
    artifact = json.loads(artifact_text)
    return type("TerminalDemoResult", (), {
        "tui_factory_call_count": len(tui_factory_calls),
        "plain_event_count": len(plain_events),
        "session_stop_reason": session_results[-1].stop_reason,
        "remaining_guidance": tui_factory_calls[0].guidance_summaries() if tui_factory_calls else (),
        "runner_guidance": consumed_guidance[-1] if consumed_guidance else (),
        "exit_code": exit_code,
        "plain_output": terminal.getvalue(),
        "artifact_text": artifact_text,
        "artifact": artifact,
        "artifact_strings": _artifact_strings(artifact),
        "animation_ticks": tuple(ticks.ticks),
        "rendered_lines": tuple(str(line[0]) for line in console.lines),
        "presentation_strings": tuple(
            str(line[0]) for line in console.lines
        ) + tuple(str(update) for update in console.status_updates),
    })()


def run_animated_tui_demo(tmp_path: Path):
    return run_terminal_demo(tmp_path, terminal=FakeTerminal(tty=True), no_animation=False)


def test_terminal_fallback_preserves_plain_harness_outcome(tmp_path: Path) -> None:
    result = run_terminal_demo(tmp_path, terminal=FakeTerminal(tty=False), no_animation=False)
    assert result.tui_factory_call_count == 0
    assert result.plain_event_count == 1
    assert result.session_stop_reason is StopReason.SUCCESS
    assert result.exit_code == 0
    assert "SafeFix 已结束：success" in result.plain_output


def test_tty_demo_consumes_fake_input_and_ticks_through_cli_adapter(tmp_path: Path) -> None:
    terminal = FakeTerminal(tty=True)
    result = run_terminal_demo(tmp_path, terminal=terminal, no_animation=False)
    assert result.tui_factory_call_count == 1
    assert result.session_stop_reason is StopReason.SUCCESS
    assert terminal.prompt_calls == 2
    assert result.runner_guidance == ()
    assert result.remaining_guidance == ()
    assert result.animation_ticks == ()
    assert any("SafeFix v0.2" in line for line in result.rendered_lines)


def test_no_animation_still_routes_tty_through_adapter_without_ticks(tmp_path: Path) -> None:
    result = run_terminal_demo(tmp_path, terminal=FakeTerminal(tty=True), no_animation=True)
    assert result.tui_factory_call_count == 1
    assert result.animation_ticks == ()


def test_artifact_contains_semantics_not_presentation_frames(tmp_path: Path) -> None:
    result = run_animated_tui_demo(tmp_path)
    assert result.artifact["stop_reason"] == "success"
    assert result.artifact["failure_sets"]["baseline"] == [
        "tests/test_semantic.py::test_semantic_artifact"
    ]
    assert result.runner_guidance == ()
    for text in result.artifact_strings:
        assert not _contains_control(text)
        assert "\x1b" not in text
        assert not any(
            identifier in text.casefold()
            for identifier in ("prompt", "presentation", "transcript", "frame")
        )
    assert "preserve the public API" not in result.artifact_strings
    assert "safefix> " not in result.artifact_strings
    assert not set(result.presentation_strings).intersection(result.artifact_strings)
