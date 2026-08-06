from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
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


@runtime_checkable
class EventSink(Protocol):
    def emit(self, event: SessionEvent) -> None:
        """Receive one already-sanitized session event."""


@dataclass(frozen=True)
class SessionEvent:
    sequence: int
    timestamp: str
    phase: Phase
    kind: str
    safe_payload: dict[str, object]

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise TypeError("event sequence must be an integer")
        if self.sequence < 0:
            raise ValueError("event sequence must not be negative")
        if not isinstance(self.phase, Phase):
            raise TypeError("event phase must be a Phase")
        if self.kind not in EVENT_KINDS:
            raise ValueError(f"unsupported event kind: {self.kind}")
        if not isinstance(self.safe_payload, dict):
            raise TypeError("event safe_payload must be a dict")
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


def _sanitize_mapping(payload: Mapping[str, object], depth: int = 0) -> dict[str, object]:
    if depth > 4:
        return {"summary": "[omitted]"}
    sanitized: dict[str, object] = {}
    for key, value in payload.items():
        safe_key = str(key)
        normalized_key = safe_key.lower().replace("-", "_").replace(" ", "_")
        if any(part in normalized_key for part in _SECRET_KEY_PARTS):
            sanitized[safe_key] = _REDACTED
        else:
            sanitized[safe_key] = _sanitize_value(value, depth + 1)
    return sanitized


def _sanitize_value(value: object, depth: int) -> object:
    if isinstance(value, Mapping):
        return _sanitize_mapping(value, depth)
    if isinstance(value, str):
        return sanitize_summary(value)
    if isinstance(value, (list, tuple)):
        if depth > 4:
            return ["[omitted]"]
        return [_sanitize_value(item, depth + 1) for item in value[:32]]
    return value
