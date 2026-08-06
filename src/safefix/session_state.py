from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from types import MappingProxyType
from typing import TypeAlias, TypeVar

from .events import (
    SessionEvent,
    sanitize_model_identity,
    sanitize_summary,
    sanitize_untrusted,
)
from .models import (
    AcceptanceMode,
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
SafeValue: TypeAlias = (
    str | int | float | bool | None | dict[str, "SafeValue"] | list["SafeValue"]
)


class SessionStateBoundaryError(ValueError):
    """Raised when untrusted session metadata crosses its typed boundary."""


@dataclass(slots=True)
class SessionState:
    F0: FailureSet
    steps: int = 0
    rounds: int = 0
    no_progress_rounds: int = 0
    test_model_identity: str | ModelRoleConfig | None = None
    repair_model_identity: str | ModelRoleConfig | None = None
    review_model_identity: str | ModelRoleConfig | None = None
    acceptance_mode: AcceptanceMode | None = None
    repair_required: bool | None = None
    F: FailureSet = field(init=False)
    U_best: FailureSet = field(init=False)
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
    preparation_summary: PreparationSummary | None = field(default=None, init=False)
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
    _high_risk_confirmation: Mapping[str, SafeValue] | None = field(
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
    def high_risk_confirmation(self) -> dict[str, SafeValue] | None:
        return (
            None
            if self._high_risk_confirmation is None
            else _thaw(self._high_risk_confirmation)
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
        object.__setattr__(self, "test_model_identity", _normalize_identity(self.test_model_identity))
        object.__setattr__(self, "repair_model_identity", _normalize_identity(self.repair_model_identity))
        object.__setattr__(self, "review_model_identity", _normalize_identity(self.review_model_identity))
        if self.acceptance_mode is not None and not isinstance(self.acceptance_mode, AcceptanceMode):
            try:
                object.__setattr__(self, "acceptance_mode", AcceptanceMode(self.acceptance_mode))
            except (TypeError, ValueError) as exc:
                raise SessionStateBoundaryError("invalid session metadata: acceptance_mode") from exc
        if self.repair_required is not None and type(self.repair_required) is not bool:
            raise SessionStateBoundaryError("invalid session metadata: repair_required")
        object.__setattr__(self, "F", self.F0)
        object.__setattr__(self, "U_best", self.F0)

    def __setattr__(self, name: str, value: object) -> None:
        if name == "F0" and hasattr(self, "F0"):
            raise AttributeError("F0 is immutable")
        if name == "last_feedback" and value is not None:
            value = _sanitize_feedback(value)
        if name in {
            "preparation_summary",
            "baseline_source",
            "manifest_hash",
            "stability_runs",
            "review_result",
        }:
            if not hasattr(self, "F") and value is None:
                super(SessionState, self).__setattr__(name, value)
                return
            if hasattr(self, name) and getattr(self, name) is not None:
                raise AttributeError(f"{name} is immutable")
            raise SessionStateBoundaryError(
                f"invalid session metadata: {name} must be set through its setter"
            )
        if name in {
            "test_model_identity",
            "repair_model_identity",
            "review_model_identity",
            "acceptance_mode",
            "repair_required",
        } and hasattr(self, name):
            raise AttributeError(f"{name} is immutable")
        if name in {
            "_high_risk_confirmation",
        } and hasattr(self, name):
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
            "_high_risk_confirmation",
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
        feedback = _sanitize_feedback(feedback)
        self._recent_tool_events = self._append_recent(
            self._recent_tool_events, (call, feedback)
        )

    def record_guard_event(self, call: ToolCall, decision: GuardDecision) -> None:
        self._recent_guard_events = self._append_recent(
            self._recent_guard_events, (call, decision)
        )

    def record_patch_fingerprint(self, fingerprint: str) -> None:
        try:
            safe_fingerprint = sanitize_summary(fingerprint)
        except (TypeError, ValueError) as exc:
            raise SessionStateBoundaryError(
                "invalid session metadata: patch fingerprint"
            ) from exc
        self._patch_fingerprints = self._patch_fingerprints | {safe_fingerprint}

    def record_event(self, event: SessionEvent) -> None:
        if not isinstance(event, SessionEvent):
            raise TypeError("event must be a SessionEvent")
        self._recent_events = self._append_recent(self._recent_events, event)

    def record_guidance(self, summary: str) -> None:
        try:
            bounded = safe_summary(summary)
        except (TypeError, ValueError) as exc:
            raise SessionStateBoundaryError("invalid session metadata: guidance") from exc
        self._guidance_event_summaries = self._append_recent(
            self._guidance_event_summaries, bounded
        )

    def set_preparation(
        self, summary: PreparationSummary, manifest: FrozenTestManifest
    ) -> None:
        if self.preparation_summary is not None:
            raise AttributeError("preparation_summary is immutable")
        if not isinstance(summary, PreparationSummary):
            raise SessionStateBoundaryError("invalid session metadata: preparation_summary")
        if not isinstance(manifest, FrozenTestManifest):
            raise SessionStateBoundaryError("invalid session metadata: manifest")
        if not isinstance(summary.baseline_source, BaselineSource):
            raise SessionStateBoundaryError("invalid session metadata: preparation_summary")
        for name in (
            "existing_test_count",
            "generated_candidate_count",
            "generated_accepted_count",
            "generated_pass_accepted",
            "generated_fail_accepted_manual",
            "generated_fail_accepted_automatic",
            "rejected_count",
            "error_count",
            "flaky_count",
        ):
            value = getattr(summary, name)
            if type(value) is not int or value < 0:
                raise SessionStateBoundaryError(
                    f"invalid session metadata: preparation_summary.{name}"
                )
        if not isinstance(manifest.baseline_source, BaselineSource):
            raise SessionStateBoundaryError("invalid session metadata: baseline_source")
        if type(manifest.stability_runs) is not int or manifest.stability_runs <= 0:
            raise SessionStateBoundaryError("invalid session metadata: stability_runs")
        if not isinstance(manifest.manifest_hash, str):
            raise SessionStateBoundaryError("invalid session metadata: manifest_hash")
        try:
            safe_manifest_hash = sanitize_summary(manifest.manifest_hash)
        except (TypeError, ValueError) as exc:
            raise SessionStateBoundaryError("invalid session metadata: manifest_hash") from exc
        object.__setattr__(self, "preparation_summary", summary)
        object.__setattr__(self, "baseline_source", manifest.baseline_source)
        object.__setattr__(self, "manifest_hash", safe_manifest_hash)
        object.__setattr__(self, "stability_runs", manifest.stability_runs)

    def set_review(self, review_result: ReviewResult) -> None:
        if self.review_result is not None:
            raise AttributeError("review_result is immutable")
        if not isinstance(review_result, ReviewResult):
            raise SessionStateBoundaryError("invalid session metadata: review_result")
        try:
            sanitized_risk = safe_summary(review_result.risk)
            sanitized_summary = safe_summary(review_result.summary)
        except (TypeError, ValueError) as exc:
            raise SessionStateBoundaryError("invalid session metadata: review_result") from exc
        object.__setattr__(
            self,
            "review_result",
            replace(
                review_result,
                risk=sanitized_risk,
                summary=sanitized_summary,
            ),
        )

    def set_high_risk_confirmation(
        self, record: HighRiskConfirmation | Mapping[str, SafeValue]
    ) -> None:
        if self._high_risk_confirmation is not None:
            raise AttributeError("high_risk_confirmation is immutable")
        if isinstance(record, HighRiskConfirmation):
            record = asdict(record)
        if not isinstance(record, Mapping):
            raise SessionStateBoundaryError(
                "invalid session metadata: high_risk_confirmation"
            )
        for name, expected_type in (("confirmed", bool), ("source", str), ("summary", str)):
            if name in record and type(record[name]) is not expected_type:
                raise SessionStateBoundaryError(
                    f"invalid session metadata: high_risk_confirmation.{name}"
                )
        sanitized = SessionEvent(
            sequence=0,
            timestamp="",
            phase=Phase.STOP,
            kind="control",
            safe_payload=record,
        ).safe_payload
        object.__setattr__(self, "_high_risk_confirmation", _freeze(sanitized))

    def update_best_checkpoint(self, failures: FailureSet) -> None:
        self.F = failures
        self.U_best = failures

    def __repr__(self) -> str:
        return (
            "SessionState("
            f"failure_count={len(self.F0.ids)}, "
            f"current_failure_count={len(self.F.ids)}, "
            f"steps={self.steps!r}, rounds={self.rounds!r}, "
            f"no_progress_rounds={self.no_progress_rounds!r}, "
            f"test_model_identity={self.test_model_identity!r}, "
            f"repair_model_identity={self.repair_model_identity!r}, "
            f"review_model_identity={self.review_model_identity!r})"
        )

    @staticmethod
    def _append_recent(events: tuple[Event, ...], event: Event) -> tuple[Event, ...]:
        return (events + (event,))[-RECENT_EVENT_LIMIT:]


def safe_summary(summary: str) -> str:
    return sanitize_summary(summary, max_chars=MAX_GUIDANCE_CHARS)


def _sanitize_feedback(value: object) -> Feedback:
    if not isinstance(value, Feedback):
        raise TypeError("feedback must be a Feedback")
    safe_labels = sanitize_untrusted(value.labels)
    if not isinstance(safe_labels, dict):
        raise SessionStateBoundaryError("invalid session metadata: feedback labels")
    return Feedback(
        outcome=safe_summary(value.outcome),
        summary=safe_summary(value.summary),
        labels=safe_labels,
    )


def _freeze(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _normalize_identity(value: str | ModelRoleConfig | None) -> str | None:
    if value is None:
        return None
    fingerprint = (
        value.identity_fingerprint if isinstance(value, ModelRoleConfig) else value
    )
    if not isinstance(fingerprint, str):
        raise SessionStateBoundaryError("invalid session metadata: model identity")
    try:
        return sanitize_model_identity(fingerprint)
    except (TypeError, ValueError) as exc:
        raise SessionStateBoundaryError("invalid session metadata: model identity") from exc
