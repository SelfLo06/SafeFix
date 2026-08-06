from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, field
import re
from typing import TypeVar

from .events import SessionEvent, sanitize_summary
from .models import (
    BaselineSource,
    Feedback,
    FailureSet,
    GuardDecision,
    HighRiskConfirmation,
    ModelRoleConfig,
    Phase,
    ToolCall,
)
from .review import ReviewResult
from .test_manifest import FrozenTestManifest
from .testprep.service import PreparationSummary


RECENT_EVENT_LIMIT = 10
MAX_GUIDANCE_CHARS = 512
Event = TypeVar("Event")


@dataclass(slots=True)
class SessionState:
    F0: FailureSet
    test_model_identity: str | None = None
    repair_model_identity: str | None = None
    review_model_identity: str | None = None
    acceptance_mode: str | None = None
    repair_required: bool | None = None
    F: FailureSet = field(init=False)
    U_best: FailureSet = field(init=False)
    steps: int = 0
    rounds: int = 0
    no_progress_rounds: int = 0
    last_evaluated: FailureSet | None = field(default=None, init=False)
    last_feedback: Feedback | None = field(default=None, init=False)
    _recent_tool_events: tuple[tuple[ToolCall, Feedback], ...] = field(
        default_factory=tuple, init=False, repr=False
    )
    _recent_guard_events: tuple[tuple[ToolCall, GuardDecision], ...] = field(
        default_factory=tuple, init=False, repr=False
    )
    _patch_fingerprints: frozenset[str] = field(
        default_factory=frozenset, init=False, repr=False
    )
    preparation_summary: object | None = field(default=None, init=False)
    baseline_source: BaselineSource | None = field(default=None, init=False)
    manifest_hash: str | None = field(default=None, init=False)
    stability_runs: int | None = field(default=None, init=False)
    review_result: ReviewResult | None = field(default=None, init=False)
    _recent_events: tuple[SessionEvent, ...] = field(
        default_factory=tuple, init=False, repr=False
    )
    _guidance_event_summaries: tuple[str, ...] = field(
        default_factory=tuple, init=False, repr=False
    )
    _high_risk_confirmation: dict[str, object] | None = field(
        default=None, init=False, repr=False
    )

    @property
    def recent_tool_events(self) -> tuple[tuple[ToolCall, Feedback], ...]:
        return self._recent_tool_events

    @property
    def recent_guard_events(self) -> tuple[tuple[ToolCall, GuardDecision], ...]:
        return self._recent_guard_events

    @property
    def patch_fingerprints(self) -> frozenset[str]:
        return self._patch_fingerprints

    @property
    def recent_events(self) -> tuple[SessionEvent, ...]:
        return self._recent_events

    @property
    def guidance_event_summaries(self) -> tuple[str, ...]:
        return self._guidance_event_summaries

    @property
    def high_risk_confirmation(self) -> dict[str, object] | None:
        return (
            None
            if self._high_risk_confirmation is None
            else deepcopy(self._high_risk_confirmation)
        )

    @property
    def preparation(self) -> PreparationSummary | None:
        return self.preparation_summary

    @property
    def review(self) -> ReviewResult | None:
        return self.review_result

    @property
    def guidance_summaries(self) -> tuple[str, ...]:
        return self.guidance_event_summaries

    @property
    def baseline_manifest_hash(self) -> str | None:
        return self.manifest_hash

    def __post_init__(self) -> None:
        self.test_model_identity = _normalize_identity(self.test_model_identity)
        self.repair_model_identity = _normalize_identity(self.repair_model_identity)
        self.review_model_identity = _normalize_identity(self.review_model_identity)
        self.F = self.F0
        self.U_best = self.F0

    def __setattr__(self, name: str, value: object) -> None:
        if name == "F0" and hasattr(self, "F0"):
            raise AttributeError("F0 is immutable")
        if name in {
            "preparation_summary",
            "baseline_source",
            "manifest_hash",
            "stability_runs",
            "review_result",
        } and hasattr(self, name) and getattr(self, name) is not None:
            raise AttributeError(f"{name} is immutable")
        super(SessionState, self).__setattr__(name, value)

    def __delattr__(self, name: str) -> None:
        if name in {
            "F0",
            "preparation_summary",
            "baseline_source",
            "manifest_hash",
            "stability_runs",
            "review_result",
        }:
            raise AttributeError(f"{name} is immutable")
        super(SessionState, self).__delattr__(name)

    def increment_step(self) -> None:
        self.steps += 1

    def increment_round(self) -> None:
        self.rounds += 1

    def increment_no_progress(self) -> None:
        self.no_progress_rounds += 1

    def reset_no_progress(self) -> None:
        self.no_progress_rounds = 0

    def record_tool_event(self, call: ToolCall, feedback: Feedback) -> None:
        self._recent_tool_events = self._append_recent(
            self._recent_tool_events, (call, feedback)
        )

    def record_guard_event(self, call: ToolCall, decision: GuardDecision) -> None:
        self._recent_guard_events = self._append_recent(
            self._recent_guard_events, (call, decision)
        )

    def record_patch_fingerprint(self, fingerprint: str) -> None:
        self._patch_fingerprints = self._patch_fingerprints | {fingerprint}

    def record_event(self, event: SessionEvent) -> None:
        if not isinstance(event, SessionEvent):
            raise TypeError("event must be a SessionEvent")
        self._recent_events = self._append_recent(self._recent_events, event)

    def record_guidance(self, summary: str) -> None:
        bounded = safe_summary(summary)
        self._guidance_event_summaries = self._append_recent(
            self._guidance_event_summaries, bounded
        )

    def set_preparation(
        self, summary: PreparationSummary, manifest: FrozenTestManifest
    ) -> None:
        if self.preparation_summary is not None:
            raise AttributeError("preparation_summary is immutable")
        if not isinstance(summary, PreparationSummary):
            raise TypeError("summary must be a PreparationSummary")
        if not isinstance(manifest, FrozenTestManifest):
            raise TypeError("manifest must be a FrozenTestManifest")
        object.__setattr__(self, "preparation_summary", summary)
        object.__setattr__(self, "baseline_source", manifest.baseline_source)
        object.__setattr__(self, "manifest_hash", manifest.manifest_hash)
        object.__setattr__(self, "stability_runs", manifest.stability_runs)

    def set_review(self, review_result: ReviewResult) -> None:
        if self.review_result is not None:
            raise AttributeError("review_result is immutable")
        if not isinstance(review_result, ReviewResult):
            raise TypeError("review_result must be a ReviewResult")
        object.__setattr__(self, "review_result", review_result)

    def set_high_risk_confirmation(
        self, record: HighRiskConfirmation | Mapping[str, object]
    ) -> None:
        if self._high_risk_confirmation is not None:
            raise AttributeError("high_risk_confirmation is immutable")
        if isinstance(record, HighRiskConfirmation):
            record = asdict(record)
        if not isinstance(record, Mapping):
            raise TypeError("high-risk confirmation must be a mapping")
        sanitized = SessionEvent(
            sequence=0,
            timestamp="",
            phase=Phase.STOP,
            kind="control",
            safe_payload=record,
        ).safe_payload
        object.__setattr__(self, "_high_risk_confirmation", sanitized)

    def update_best_checkpoint(self, failures: FailureSet) -> None:
        self.F = failures
        self.U_best = failures

    @staticmethod
    def _append_recent(events: tuple[Event, ...], event: Event) -> tuple[Event, ...]:
        return (events + (event,))[-RECENT_EVENT_LIMIT:]


def safe_summary(summary: str) -> str:
    sanitized = sanitize_summary(summary, max_chars=MAX_GUIDANCE_CHARS)
    sanitized = re.sub(r"(?i)authorization\s*:\s*", "[REDACTED] ", sanitized)
    lowered = sanitized.lower()
    if any(
        marker in lowered
        for marker in (
            "raw model response",
            "full source response",
            "complete source",
            "source code",
        )
    ):
        return "[REDACTED]"
    return sanitized


def _normalize_identity(value: str | ModelRoleConfig | None) -> str | None:
    if value is None:
        return None
    fingerprint = (
        value.identity_fingerprint if isinstance(value, ModelRoleConfig) else value
    )
    if not isinstance(fingerprint, str):
        raise TypeError("model identity must be a string or ModelRoleConfig")
    return sanitize_summary(fingerprint, max_chars=512)
