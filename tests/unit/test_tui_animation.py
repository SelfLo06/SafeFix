from __future__ import annotations

from safefix.events import SessionEvent
from safefix.models import Phase, SessionResult, StopReason
from safefix.operator import OperatorCommandQueue
from safefix.tui import GuidedRepairConsole, TerminalCapabilities, animation_frames
from tests.fixtures.tui.fake_terminal import FakeConsole, FakePromptSession, FakeTickSource


class FakeController:
    def run(self) -> SessionResult:
        return SessionResult(StopReason.REQUESTED)


def _console(*, animation: bool, ticks: FakeTickSource) -> GuidedRepairConsole:
    return GuidedRepairConsole(OperatorCommandQueue(), lambda _sink, _queue: FakeController(), lambda: FakePromptSession(), FakeConsole(), TerminalCapabilities(True, True, True, animation), ticks)


def _event(sequence: int, summary: str, *, status: str | None = None) -> SessionEvent:
    payload: dict[str, object] = {"summary": summary}
    if status is not None:
        payload["status"] = status
    return SessionEvent(sequence, "2026-08-07T00:00:00Z", Phase.EVALUATE, "pytest", payload)


def test_animation_uses_fake_ticks_and_never_changes_event_order() -> None:
    ticks = FakeTickSource([0, 1, 2])
    console = _console(animation=True, ticks=ticks)
    console.publish(_event(1, "pytest running", status="running"))
    console.publish(_event(2, "better: 3 -> 2"))
    console.drain_events_once()
    assert console.rendered_event_sequences == [1, 2]
    assert ticks.sleep_calls == 0


def test_disabled_animation_renders_only_a_final_frame() -> None:
    frames = animation_frames(_event(1, "pytest running", status="running"), TerminalCapabilities(True, True, True, False))
    assert len(frames) == 1
    assert "pytest running" in frames[0].text


def test_enabled_animation_updates_transient_status_once_per_tick() -> None:
    ticks = FakeTickSource([0, 1, 2])
    console_output = FakeConsole()
    console = GuidedRepairConsole(
        OperatorCommandQueue(),
        lambda _sink, _queue: FakeController(),
        FakePromptSession,
        console_output,
        TerminalCapabilities(True, True, True, True),
        ticks,
    )
    console.publish(_event(1, "pytest running", status="running"))

    console.drain_events_once()

    assert ticks.ticks == [0, 1, 2]
    assert console_output.status_updates == ["[TEST] pytest running.", "[TEST] pytest running..", "[TEST] pytest running..."]
    assert [line for line, _style in console_output.lines if "pytest running" in str(line)] == ["[TEST] pytest running"]
