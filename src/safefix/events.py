from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice
from math import isfinite
import re
from typing import Protocol, runtime_checkable

from .models import Phase


EVENT_KINDS = frozenset(
    {
        "intake",
        "test-discovery",
        "candidate",
        "stability-run",
        "acceptance",
        "guidance",
        "control",
        "model-call",
        "tool",
        "guardrail",
        "patch",
        "pytest",
        "rollback",
        "review",
        "terminal",
    }
)
MAX_SAFE_SUMMARY_CHARS = 512
MAX_SAFE_COLLECTION_ITEMS = 32
MAX_SAFE_NUMERIC_MAGNITUDE = 10**12
_REDACTED = "[REDACTED]"
_SECRET_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "access_token",
    "refresh_token",
    "secret",
    "password",
    "credential",
    "model_response",
    "raw_response",
    "response",
    "prompt",
    "completion",
    "source_code",
    "complete_source",
)
_SECRET_TEXT_RE = re.compile(
    r"(?i)(?:bearer\s+|(?:api[_ -]?key|access[_ -]?token|password|secret)\s*[:=]\s*|sk-[a-z0-9_-]{8,})\S*"
)


class _ImmutableMapping(tuple, Mapping[str, object]):
    """Immutable mapping entries that the stdlib JSON encoder can traverse.

    The tuple stores ``(key, value)`` entries, so JSON encodes the safe payload
    as a bounded array without requiring a custom encoder. Mapping access is
    retained for event consumers; all stored values are themselves immutable.
    """

    __slots__ = ()

    def __new__(
        cls, items: tuple[tuple[str, object], ...]
    ) -> _ImmutableMapping:
        return tuple.__new__(cls, items)

    def __getitem__(self, key: str | int | slice) -> object:
        if isinstance(key, (int, slice)):
            return tuple.__getitem__(self, key)
        for item_key, value in tuple.__iter__(self):
            if item_key == key:
                return value
        raise KeyError(key)

    def __iter__(self):
        # Keep entry iteration so json.dumps can encode this tuple directly.
        return tuple.__iter__(self)

    def __len__(self) -> int:
        return tuple.__len__(self)

    def __contains__(self, key: object) -> bool:
        return any(item_key == key for item_key, _ in tuple.__iter__(self))

    def items(self):
        return tuple.__iter__(self)

    def keys(self):
        return tuple(item_key for item_key, _ in tuple.__iter__(self))

    def values(self):
        return tuple(value for _, value in tuple.__iter__(self))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return dict(self.items()) == dict(other.items())
        return tuple.__eq__(self, other)

    def __repr__(self) -> str:
        return repr(dict(self.items()))


@runtime_checkable
class EventSink(Protocol):
    def emit(self, event: SessionEvent) -> None:
        """Receive one already-sanitized session event."""


class LegacyEventSinkAdapter:
    """Forward legacy runner text events to one typed sink."""

    def __init__(self, sink: EventSink) -> None:
        self._sink = sink
        self._sequence = 1

    def __call__(self, summary: str) -> None:
        self._sink.emit(
            SessionEvent(
                sequence=self._sequence,
                timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                phase=Phase.READY,
                kind="control",
                safe_payload={"summary": summary},
            )
        )
        self._sequence += 1


@dataclass(frozen=True)
class SessionEvent:
    sequence: int
    timestamp: str
    phase: Phase
    kind: str
    safe_payload: Mapping[str, object]

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise TypeError("event sequence must be an integer")
        if self.sequence < 0:
            raise ValueError("event sequence must not be negative")
        if not isinstance(self.phase, Phase):
            raise TypeError("event phase must be a Phase")
        if self.kind not in EVENT_KINDS:
            raise ValueError(f"unsupported event kind: {self.kind}")
        if not isinstance(self.safe_payload, Mapping):
            raise TypeError("event safe_payload must be a mapping")
        object.__setattr__(
            self,
            "safe_payload",
            _sanitize_mapping(self.safe_payload),
        )


def sanitize_summary(text: str, *, max_chars: int = MAX_SAFE_SUMMARY_CHARS) -> str:
    """Return a bounded summary without common credential or response material."""
    if not isinstance(text, str):
        raise TypeError("summary must be a string")
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    redacted = _SECRET_TEXT_RE.sub(_REDACTED, text)
    if len(redacted) <= max_chars:
        return redacted
    return redacted[:max_chars]


def _sanitize_mapping(
    payload: Mapping[str, object], depth: int = 0
) -> _ImmutableMapping:
    if depth > 4:
        return _ImmutableMapping((("summary", "[omitted]"),))
    sanitized: dict[str, object] = {}
    for key, value in islice(payload.items(), MAX_SAFE_COLLECTION_ITEMS):
        safe_key = _sanitize_key(key)
        normalized_key = safe_key.lower().replace("-", "_").replace(" ", "_")
        if any(part in normalized_key for part in _SECRET_KEY_PARTS):
            sanitized[safe_key] = _REDACTED
        else:
            sanitized[safe_key] = _sanitize_value(value, depth + 1)
    return _ImmutableMapping(tuple(sanitized.items()))


def _sanitize_key(key: object) -> str:
    if not isinstance(key, str):
        return _REDACTED
    return sanitize_summary(key)


def _sanitize_value(value: object, depth: int) -> object:
    if isinstance(value, Mapping):
        return _sanitize_mapping(value, depth)
    if isinstance(value, str):
        return sanitize_summary(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return _REDACTED
    if isinstance(value, (list, tuple)):
        if depth > 4:
            return ("[omitted]",)
        return tuple(
            _sanitize_value(item, depth + 1)
            for item in value[:MAX_SAFE_COLLECTION_ITEMS]
        )
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if -MAX_SAFE_NUMERIC_MAGNITUDE <= value <= MAX_SAFE_NUMERIC_MAGNITUDE:
            return value
        return _REDACTED
    if type(value) is float:
        if isfinite(value) and abs(value) <= MAX_SAFE_NUMERIC_MAGNITUDE:
            return value
        return _REDACTED
    return _REDACTED
