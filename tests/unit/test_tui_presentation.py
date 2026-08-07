from __future__ import annotations

from safefix.events import SessionEvent
from safefix.models import Phase
from safefix.tui import TerminalCapabilities, render_event, terminal_capabilities
from tests.fixtures.tui.fake_terminal import FakeStream


def test_terminal_capabilities_disable_animation_without_tty_or_with_no_animation() -> None:
    assert terminal_capabilities(FakeStream(tty=False), FakeStream(tty=False), {}, False, False).animation is False
    assert terminal_capabilities(FakeStream(tty=True), FakeStream(tty=True), {}, True, False).animation is False


def test_terminal_capabilities_honor_no_color_and_dumb_terminal() -> None:
    no_color = terminal_capabilities(FakeStream(tty=True), FakeStream(tty=True), {"NO_COLOR": "1"}, False, False)
    dumb = terminal_capabilities(FakeStream(tty=True), FakeStream(tty=True), {"TERM": "dumb"}, False, False)
    assert no_color.color is False
    assert dumb.color is False
    assert dumb.animation is False


def test_transcript_renderer_uses_safe_payload_and_ascii_fallback() -> None:
    event = SessionEvent(1, "2026-08-07T00:00:00Z", Phase.EVALUATE, "pytest", {"summary": "better: 3 -> 2", "secret": "not-rendered"})
    entry = render_event(event, TerminalCapabilities(True, False, False, False))
    assert entry.text.startswith("[TEST]")
    assert "better: 3 -> 2" in entry.text
    assert "not-rendered" not in entry.text


def test_transcript_renderer_uses_unicode_marker_when_supported() -> None:
    event = SessionEvent(2, "2026-08-07T00:00:00Z", Phase.READY, "guardrail", {"summary": "safe"})
    entry = render_event(event, TerminalCapabilities(True, True, True, False))
    assert entry.text.startswith("[GUARD] ✓")
