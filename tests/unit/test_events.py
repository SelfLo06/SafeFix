from dataclasses import FrozenInstanceError
import json

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
    assert event.safe_payload["[REDACTED_KEY_1]"] == "[REDACTED]"
    assert event.safe_payload["[REDACTED_KEY_2]"] == "[REDACTED]"
    assert event.safe_payload["[REDACTED_KEY_3]"] == "[REDACTED]"
    assert event.safe_payload["nested"] == {"[REDACTED_KEY_0]": "[REDACTED]"}
    assert "sk-live-secret" not in repr(event)
    assert "complete source" not in repr(event)


def test_session_event_redacts_sensitive_keys_values_urls_and_timestamps() -> None:
    event = SessionEvent(
        sequence=11,
        timestamp="not-a-safe-timestamp TOPSECRET",
        phase=Phase.DISPATCH,
        kind="model-call",
        safe_payload={
            "nested": {
                "endpoint": "https://user:pass@example.test/private?query=secret",
                "query": "user=alice",
                "token": "TOKENSECRET",
                "raw": "def source(): return 'SOURCESECRET'",
                "safe": "bounded status",
            }
        },
    )

    rendered = repr(event)
    assert "TOPSECRET" not in rendered
    assert "user:pass" not in rendered
    assert "/private" not in rendered
    assert "query=secret" not in rendered
    assert "TOKENSECRET" not in rendered
    assert "SOURCESECRET" not in rendered
    assert "endpoint" not in rendered
    assert "query" not in rendered
    assert "token" not in rendered
    assert "raw" not in rendered
    assert event.safe_payload["nested"]["safe"] == "bounded status"


def test_session_event_redacts_unkeyed_secret_code_and_exception_values() -> None:
    event = SessionEvent(
        sequence=12,
        timestamp="2026-08-06T12:00:00Z",
        phase=Phase.DISPATCH,
        kind="tool",
        safe_payload={
            "status": "TOKENSECRET",
            "source": "SOURCESECRET",
            "output": "print(SOURCESECRET)",
            "error": "Traceback(TOKENSECRET)",
            "exception": "Exception(TOKENSECRET)",
            "safe_count": 2,
        },
    )

    rendered = repr(event)
    for secret in ("TOKENSECRET", "SOURCESECRET", "print(", "Traceback", "Exception"):
        assert secret not in rendered
    assert event.safe_payload["safe_count"] == 2


def test_session_event_redacts_bytes() -> None:
    event = SessionEvent(
        sequence=2,
        timestamp="2026-08-06T12:00:00Z",
        phase=Phase.DISPATCH,
        kind="model-call",
        safe_payload={"attachment": b"sk-raw-secret-leaks-through-bytes"},
    )

    assert event.safe_payload["attachment"] == "[REDACTED]"
    assert "sk-raw-secret-leaks-through-bytes" not in repr(event)


def test_session_event_redacts_unknown_objects_without_rendering_them() -> None:
    class SecretBearingObject:
        def __repr__(self) -> str:
            return "SecretBearingObject(sk-raw-secret-leaks-through-repr)"

    event = SessionEvent(
        sequence=3,
        timestamp="2026-08-06T12:00:00Z",
        phase=Phase.DISPATCH,
        kind="model-call",
        safe_payload={"detail": SecretBearingObject()},
    )

    assert event.safe_payload["detail"] == "[REDACTED]"
    assert "sk-raw-secret-leaks-through-repr" not in repr(event)


def test_session_event_safe_payload_cannot_be_mutated_after_construction() -> None:
    event = SessionEvent(
        sequence=4,
        timestamp="2026-08-06T12:00:00Z",
        phase=Phase.READY,
        kind="control",
        safe_payload={"summary": "safe"},
    )

    event.safe_payload["raw_response"] = "sk-raw-secret-injected-later"

    assert "raw_response" not in event.safe_payload
    assert "sk-raw-secret-injected-later" not in repr(event)


def test_session_event_nested_safe_payload_cannot_be_mutated() -> None:
    event = SessionEvent(
        sequence=6,
        timestamp="2026-08-06T12:00:00Z",
        phase=Phase.READY,
        kind="control",
        safe_payload={"nested": {"items": [{"summary": "safe"}]}},
    )

    event.safe_payload["nested"]["raw_response"] = "secret"
    event.safe_payload["nested"]["items"][0]["raw_response"] = "secret"

    assert "raw_response" not in event.safe_payload["nested"]
    assert "raw_response" not in event.safe_payload["nested"]["items"][0]


def test_session_event_safe_payload_rejects_dict_mutation_bypass() -> None:
    event = SessionEvent(
        sequence=7,
        timestamp="2026-08-06T12:00:00Z",
        phase=Phase.READY,
        kind="control",
        safe_payload={"nested": {"summary": "safe"}},
    )

    dict.__setitem__(event.safe_payload, "raw_response", "secret")
    dict.__setitem__(event.safe_payload["nested"], "raw_response", "secret")

    assert "raw_response" not in event.safe_payload
    assert "raw_response" not in event.safe_payload["nested"]
    assert "secret" not in repr(event)


def test_session_event_sanitizes_huge_scalar_values() -> None:
    event = SessionEvent(
        sequence=8,
        timestamp="2026-08-06T12:00:00Z",
        phase=Phase.READY,
        kind="control",
        safe_payload={"huge": 10**5000, "negative": -(10**5000)},
    )

    assert event.safe_payload["huge"] == "[REDACTED]"
    assert event.safe_payload["negative"] == "[REDACTED]"
    json.dumps(event.safe_payload)


def test_session_event_safe_payload_remains_json_compatible() -> None:
    event = SessionEvent(
        sequence=5,
        timestamp="2026-08-06T12:00:00Z",
        phase=Phase.READY,
        kind="control",
        safe_payload={"nested": ["summary", {"count": 2}]},
    )

    assert json.loads(json.dumps(event.safe_payload)) == {
        "nested": ["summary", {"count": 2}]
    }


def test_session_event_safe_payload_is_a_plain_dict_with_normal_key_iteration() -> None:
    event = SessionEvent(
        sequence=9,
        timestamp="2026-08-06T12:00:00Z",
        phase=Phase.READY,
        kind="control",
        safe_payload={"first": 1, "second": 2},
    )

    assert isinstance(event.safe_payload, dict)
    assert list(event.safe_payload) == ["first", "second"]


def test_session_event_safe_payload_mutations_are_isolated_from_event_snapshot() -> None:
    event = SessionEvent(
        sequence=10,
        timestamp="2026-08-06T12:00:00Z",
        phase=Phase.READY,
        kind="control",
        safe_payload={"nested": {"items": [{"summary": "safe"}]}},
    )

    returned_payload = event.safe_payload
    returned_payload["new"] = "sk-injected-secret"
    returned_payload["nested"]["items"][0]["summary"] = "changed"
    returned_payload["nested"]["items"].append({"raw_response": "secret"})

    assert event.safe_payload == {"nested": {"items": [{"summary": "safe"}]}}
    assert "sk-injected-secret" not in repr(event)
    assert "changed" not in repr(event)
    assert "secret" not in repr(event)


def test_event_sink_requires_typed_emit_method() -> None:
    class InvalidSink:
        pass

    assert isinstance(RecordingSink(), EventSink)
    assert not isinstance(InvalidSink(), EventSink)
