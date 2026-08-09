from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import islice
from math import isfinite
import re
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

from .models import Phase


EVENT_KINDS = frozenset(
    {
        "intake",
        "test-discovery",
        "candidate",
        "stability-run",
        "acceptance",
        "approval",
        "guidance",
        "explain",
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
MAX_RAW_LOG_CHARS = 12000
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
_SENSITIVE_KEY_PARTS = _SECRET_KEY_PARTS + (
    "token",
    "raw",
    "endpoint",
    "query",
    "auth",
    "userinfo",
    "source",
)
_SECRET_TEXT_RE = re.compile(
    r"(?i)(?:bearer\s+|(?:api[_ -]?key|access[_ -]?token|password|secret)\s*[:=]\s*|sk-[a-z0-9_-]{8,})\S*"
)
_SENSITIVE_TEXT_RE = re.compile(
    r"(?i)\b(?:bearer|password|secret|token|credential|authorization|"
    r"api[_ -]?key|access[_ -]?token|refresh[_ -]?token)\b|"
    r"raw[-_ ]?response|traceback|exception"
)
_UNMARKED_SECRET_RE = re.compile(
    r"(?i:(?:\b(?:token|secret|source|apikey|api_key|bearer|password|credential)"
    r"[A-Z0-9_-]{2,}\b|\b[A-Z0-9_-]{2,}(?:token|secret|apikey|password)\b))|"
    r"\b[A-Z]{8,}\b"
)
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:token|secret|auth(?:entication|orization)?|endpoint|query|userinfo)\s*[:=]\s*\S+"
)
_QUERY_RE = re.compile(r"(?:[?&][A-Za-z0-9_.-]+=[^\s&]+)")
_USERINFO_RE = re.compile(r"\b[^\s/:@]+:[^\s/@]+@")
_SAFE_IDENTITY_RE = re.compile(
    r"^(?:test|repair|review):https?://[a-z0-9.-]+(?::\d+)?:[a-z0-9_.-]+$"
)


class _ImmutableMapping(tuple, Mapping[str, object]):
    """Immutable mapping snapshot used behind the public payload property.

    The tuple stores ``(key, value)`` entries. It is never exposed directly;
    event consumers receive a fresh ordinary dictionary instead.
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
    _raw_text: str | None = field(init=False, repr=False, compare=False, default=None)
    _safe_payload_snapshot: _ImmutableMapping = field(
        init=False, repr=False, compare=False
    )

    def __init__(
        self,
        sequence: int,
        timestamp: str,
        phase: Phase,
        kind: str,
        safe_payload: Mapping[str, object],
        raw_text: str | None = None,
    ) -> None:
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            raise TypeError("event sequence must be an integer")
        if sequence < 0:
            raise ValueError("event sequence must not be negative")
        if not isinstance(phase, Phase):
            raise TypeError("event phase must be a Phase")
        if kind not in EVENT_KINDS:
            raise ValueError(f"unsupported event kind: {kind}")
        if not isinstance(safe_payload, Mapping):
            raise TypeError("event safe_payload must be a mapping")
        object.__setattr__(
            self,
            "sequence",
            sequence,
        )
        object.__setattr__(self, "timestamp", sanitize_timestamp(timestamp))
        object.__setattr__(self, "phase", phase)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "_raw_text", sanitize_raw_log(raw_text) if raw_text is not None else None)
        object.__setattr__(self, "_safe_payload_snapshot", _sanitize_mapping(safe_payload))

    @property
    def raw_text(self) -> str | None:
        return self._raw_text

    @property
    def safe_payload(self) -> dict[str, object]:
        """Return a fresh JSON-compatible copy of the sanitized payload."""
        return _materialize_mapping(self._safe_payload_snapshot)

    def __repr__(self) -> str:
        return (
            "SessionEvent("
            f"sequence={self.sequence!r}, "
            f"timestamp={self.timestamp!r}, "
            f"phase={self.phase!r}, "
            f"kind={self.kind!r}, "
            f"safe_payload={self.safe_payload!r})"
        )


def sanitize_summary(text: str, *, max_chars: int = MAX_SAFE_SUMMARY_CHARS) -> str:
    """Return a bounded conservative projection of untrusted text."""
    if not isinstance(text, str):
        raise TypeError("summary must be a string")
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if _SAFE_IDENTITY_RE.fullmatch(text):
        return text
    text = re.sub(r"[\r\n]+", " ", text).strip()
    if (
        _looks_like_code(text)
        or _looks_like_url(text)
        or _looks_like_traceback(text)
        or _looks_like_raw_response(text)
    ):
        return _REDACTED
    if _QUERY_RE.search(text) or _USERINFO_RE.search(text):
        return _REDACTED
    if _UNMARKED_SECRET_RE.search(text):
        return _REDACTED
    if _SENSITIVE_TEXT_RE.search(text):
        return _REDACTED
    redacted = _SECRET_TEXT_RE.sub(_REDACTED, text)
    redacted = _SENSITIVE_ASSIGNMENT_RE.sub(_REDACTED, redacted)
    redacted = re.sub(r"(?i)authorization\s*:\s*", "[REDACTED] ", redacted)
    lowered = redacted.lower()
    if any(
        marker in lowered
        for marker in (
            "raw model response",
            "full source response",
            "complete source",
            "source code",
        )
    ):
        return _REDACTED
    if len(redacted) <= max_chars:
        return redacted
    return redacted[:max_chars]


def sanitize_raw_log(text: str, *, max_chars: int = MAX_RAW_LOG_CHARS) -> str:
    if not isinstance(text, str):
        raise TypeError("raw log must be a string")
    redacted = _SECRET_TEXT_RE.sub(_REDACTED, text)
    redacted = re.sub(r"(?i)authorization\s*:\s*[^\r\n]+", "authorization: [REDACTED]", redacted)
    if len(redacted) > max_chars:
        return redacted[:max_chars] + "\n[raw response truncated]"
    return redacted


def _sanitize_mapping(
    payload: Mapping[str, object], depth: int = 0
) -> _ImmutableMapping:
    if depth > 4:
        return _ImmutableMapping((("summary", "[omitted]"),))
    sanitized: dict[str, object] = {}
    for index, (key, value) in enumerate(
        islice(payload.items(), MAX_SAFE_COLLECTION_ITEMS)
    ):
        safe_key = _sanitize_key(key)
        normalized_key = safe_key.lower().replace("-", "_").replace(" ", "_")
        if _is_sensitive_key(normalized_key):
            safe_key = f"[REDACTED_KEY_{index}]"
            sanitized[safe_key] = _REDACTED
        else:
            sanitized[safe_key] = _sanitize_value(value, depth + 1)
    return _ImmutableMapping(tuple(sanitized.items()))


def _materialize_mapping(payload: _ImmutableMapping) -> dict[str, object]:
    return {
        key: _materialize_value(value)
        for key, value in tuple.__iter__(payload)
    }


def _materialize_value(value: object) -> object:
    if isinstance(value, _ImmutableMapping):
        return _materialize_mapping(value)
    if isinstance(value, tuple):
        return [_materialize_value(item) for item in value]
    if isinstance(value, list):
        return [_materialize_value(item) for item in value]
    return value


def _sanitize_key(key: object) -> str:
    if not isinstance(key, str):
        return _REDACTED
    normalized = key.lower().replace("-", "_").replace(" ", "_")
    if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
        return key[:MAX_SAFE_SUMMARY_CHARS]
    return sanitize_summary(key)


def _is_sensitive_key(normalized_key: str) -> bool:
    return normalized_key in {"source", "userinfo"} or any(
        part in normalized_key
        for part in _SENSITIVE_KEY_PARTS
        if part not in {"source", "userinfo"}
    )


def sanitize_timestamp(value: str) -> str:
    """Keep only a canonical UTC timestamp; invalid input is not retained."""
    if not isinstance(value, str):
        raise TypeError("timestamp must be a string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return _REDACTED
    if parsed.tzinfo is None:
        return _REDACTED
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def sanitize_model_identity(value: str) -> str:
    """Return ``role:provider-origin:model`` without URL credentials or paths."""
    if not isinstance(value, str):
        raise TypeError("model identity must be a string")
    prefix, separator, model = value.rpartition(":")
    role, role_separator, endpoint = prefix.partition(":")
    if role_separator and role and endpoint.startswith(("http://", "https://")):
        safe_endpoint = _safe_url_origin(endpoint)
        if safe_endpoint != _REDACTED:
            return f"{role}:{safe_endpoint}:{sanitize_summary(model)}"
    return sanitize_summary(value)


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


def sanitize_untrusted(value: object) -> object:
    """Sanitize one untrusted JSON-like value recursively.

    Safe scalar types remain available for counters, flags, and enum values;
    untrusted strings and unknown objects go through the same text policy.
    """
    return _materialize_value(_sanitize_value(value, 0))


def _looks_like_code(value: str) -> bool:
    return bool(
        re.search(
            r"(?i)(?:^|\s)(?:def|class|import|from|async|await|function)\s+|"
            r"(?:^|\s)return\s+(?:['\"`]|[-+]?\d|[A-Za-z_]\w*\s*[([{=])|"
            r"(?:=>|```|[{};])|(?:\bprint|pprint)\s*\(",
            value,
        )
    )


def _looks_like_traceback(value: str) -> bool:
    return bool(re.search(r"(?i)\b(?:traceback|[a-z_]\w*(?:error|exception))\s*[(\[]", value))


def _looks_like_raw_response(value: str) -> bool:
    return bool(re.search(r"(?i)\b(?:raw|full|unredacted)[-_ ]?(?:model[-_ ]?)?response\b", value))


def _looks_like_url(value: str) -> bool:
    return bool(re.search(r"(?i)https?://[^\s<>\"']+", value))


def _safe_url_origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        if parsed.scheme not in {"http", "https"} or not hostname:
            return _REDACTED
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        return _REDACTED
    return f"{parsed.scheme.lower()}://{hostname.lower()}{port}"
