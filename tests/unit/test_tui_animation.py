from __future__ import annotations

import time

from safefix.events import SessionEvent
from safefix.models import Phase, SessionResult, StopReason
from safefix.operator import OperatorCommandQueue
from safefix.tui import GuidedRepairConsole, TerminalCapabilities
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


def test_enabled_animation_uses_a_prompt_toolbar_spinner() -> None:
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

    assert ticks.ticks == []
    assert "正在验证" in console._activity_toolbar()


def test_running_event_keeps_toolbar_visible_until_a_terminal_event() -> None:
    console_output = FakeConsole()
    console = GuidedRepairConsole(
        OperatorCommandQueue(),
        lambda _sink, _queue: FakeController(),
        FakePromptSession,
        console_output,
        TerminalCapabilities(True, True, True, True),
        FakeTickSource([]),
    )

    console.publish(_event(1, "Repair Model request in progress", status="running"))
    console.drain_events_once()

    assert "正在分析" in console._activity_toolbar()

    console.publish(_event(2, "Repair Model response received", status="completed"))
    console.drain_events_once()

    assert console._activity_toolbar() == ""


def test_activity_toolbar_uses_product_phase_not_internal_dispatch() -> None:
    console = _console(animation=True, ticks=FakeTickSource([]))
    console.publish(_event(1, "Repair Model request 2 of 3 in progress.", status="running"))

    console.drain_events_once()

    assert "正在分析" in console._activity_toolbar()
    assert "dispatch" not in console._activity_toolbar()


def test_activity_toolbar_labels_test_model_generation() -> None:
    console = _console(animation=True, ticks=FakeTickSource([]))
    console.publish(
        SessionEvent(
            1,
            "2026-08-07T00:00:00Z",
            Phase.TEST_PREPARATION,
            "model-call",
            {
                "role": "test",
                "status": "running",
                "summary": "Test Model request in progress.",
            },
        )
    )

    console.drain_events_once()

    assert "正在生成测试" in console._activity_toolbar()


def test_activity_toolbar_refreshes_the_current_request_duration(
    monkeypatch,
) -> None:
    console = _console(animation=True, ticks=FakeTickSource([]))
    console.publish(_event(1, "Repair Model request 1 of 3 in progress.", status="running"))
    console.drain_events_once()
    console._operation_started_at = time.monotonic() - 5

    assert "5s" in console._activity_toolbar()
