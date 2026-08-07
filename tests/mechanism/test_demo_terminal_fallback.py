from pathlib import Path
import json

from safefix.models import StopReason
from safefix.artifacts import ArtifactWriter
from safefix.events import SessionEvent
from safefix.models import FailureSet, Phase, SessionResult
from safefix.operator import OperatorCommandQueue
from safefix.session_state import SessionState
from safefix.tui import GuidedRepairConsole, TerminalCapabilities, animation_frames, terminal_capabilities
from tests.fixtures.tui.fake_terminal import FakeConsole, FakePromptSession, FakeTickSource

def run_terminal_demo(tmp_path: Path, *, terminal, no_animation: bool):
    capabilities = terminal_capabilities(
        terminal, terminal, {}, no_animation, test_mode=True
    )
    console = FakeConsole()
    adapter = GuidedRepairConsole(
        command_queue=OperatorCommandQueue(),
        controller_factory=lambda _sink, _queue: type(
            "Controller", (), {"run": lambda _self: SessionResult(StopReason.SUCCESS)}
        )(),
        input_factory=FakePromptSession,
        console=console,
        capabilities=capabilities,
        tick_source=FakeTickSource([]),
    )
    result = adapter.run()
    return type(
        "TerminalDemoResult", (), {
            "tui_started": capabilities.interactive,
            "transcript_is_plain": not capabilities.interactive,
            "stop_reason": result.stop_reason,
            "artifact_text": json.dumps({"stop_reason": result.stop_reason.name}),
            "animation_frames": len(animation_frames(
                SessionEvent(1, "2026-08-07T00:00:00Z", Phase.EVALUATE, "pytest", {"summary": "running", "status": "running"}),
                capabilities,
            )),
        },
    )()


def run_animated_tui_demo(tmp_path: Path):
    ticks = FakeTickSource([0, 1, 2])
    capabilities = TerminalCapabilities(True, True, True, True)
    console = FakeConsole()
    adapter = GuidedRepairConsole(
        OperatorCommandQueue(),
        lambda _sink, _queue: type(
            "Controller", (), {"run": lambda _self: SessionResult(StopReason.SUCCESS)}
        )(),
        FakePromptSession,
        console,
        capabilities,
        ticks,
    )
    adapter.publish(SessionEvent(1, "2026-08-07T00:00:00Z", Phase.EVALUATE, "pytest", {"summary": "running", "status": "running"}))
    adapter.drain_events_once()
    artifact_path = tmp_path / "safefix-session.json"
    ArtifactWriter(artifact_path).write(
        SessionState(FailureSet(frozenset())), SessionResult(StopReason.SUCCESS)
    )
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    return type("AnimatedDemoResult", (), {"artifact": artifact, "ticks": len(ticks.ticks)})()


def test_terminal_fallback_preserves_plain_harness_outcome(tmp_path: Path) -> None:
    result = run_terminal_demo(tmp_path, terminal=FakeTerminal(tty=False), no_animation=False)

    assert result.tui_started is False
    assert result.transcript_is_plain is True
    assert result.stop_reason is StopReason.SUCCESS
    assert "ANSI" not in result.artifact_text


def test_no_animation_still_uses_tty_adapter_without_frames(tmp_path: Path) -> None:
    result = run_terminal_demo(tmp_path, terminal=FakeTerminal(tty=True), no_animation=True)

    assert result.tui_started is True
    assert result.animation_frames == 1
    assert result.stop_reason is StopReason.SUCCESS


def test_artifact_contains_semantics_not_presentation_frames(tmp_path: Path) -> None:
    artifact = run_animated_tui_demo(tmp_path).artifact

    assert artifact["stop_reason"] == "success"
    assert "spinner" not in repr(artifact)
    assert "ANSI" not in repr(artifact)


class FakeTerminal:
    def __init__(self, *, tty: bool) -> None:
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty
