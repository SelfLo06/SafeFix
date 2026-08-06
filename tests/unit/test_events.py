from dataclasses import FrozenInstanceError

import pytest

from safefix.models import Phase
from safefix.events import EventSink, SessionEvent


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[SessionEvent] = []

    def emit(self, event: SessionEvent) -> None:
        self.events.append(event)


def test_session_event_is_frozen_and_preserves_sequence() -> None:
    event = SessionEvent(
        sequence=17,
        timestamp="2026-08-06T12:00:00Z",
        phase=Phase.READY,
        kind="control",
        safe_payload={"command": "status"},
    )

    assert event.sequence == 17
    assert event.phase is Phase.READY
    with pytest.raises(FrozenInstanceError):
        event.sequence = 18  # type: ignore[misc]

    sink = RecordingSink()
    sink.emit(event)
    assert sink.events == [event]
    assert isinstance(sink, EventSink)


def test_session_event_redacts_secrets_and_unbounded_content() -> None:
    event = SessionEvent(
        sequence=1,
        timestamp="2026-08-06T12:00:00Z",
        phase=Phase.DISPATCH,
        kind="model-call",
        safe_payload={
            "summary": "model requested a source read",
            "api_key": "sk-live-secret",
            "authorization": "Bearer sk-live-secret",
            "source_code": "def secret():\n    return 'complete source'",
            "nested": {"model_response": "full unredacted response"},
        },
    )

    assert event.safe_payload["summary"] == "model requested a source read"
    assert event.safe_payload["api_key"] == "[REDACTED]"
    assert event.safe_payload["authorization"] == "[REDACTED]"
    assert event.safe_payload["source_code"] == "[REDACTED]"
    assert event.safe_payload["nested"] == {"model_response": "[REDACTED]"}
    assert "sk-live-secret" not in repr(event)
    assert "complete source" not in repr(event)


def test_event_sink_requires_typed_emit_method() -> None:
    class InvalidSink:
        pass

    assert isinstance(RecordingSink(), EventSink)
    assert not isinstance(InvalidSink(), EventSink)
