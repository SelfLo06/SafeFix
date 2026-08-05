from dataclasses import dataclass, field
from typing import TypeVar

from .models import Feedback, FailureSet, GuardDecision, ToolCall


RECENT_EVENT_LIMIT = 10
Event = TypeVar("Event")


@dataclass(slots=True)
class SessionState:
    F0: FailureSet
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

    @property
    def recent_tool_events(self) -> tuple[tuple[ToolCall, Feedback], ...]:
        return self._recent_tool_events

    @property
    def recent_guard_events(self) -> tuple[tuple[ToolCall, GuardDecision], ...]:
        return self._recent_guard_events

    @property
    def patch_fingerprints(self) -> frozenset[str]:
        return self._patch_fingerprints

    def __post_init__(self) -> None:
        self.F = self.F0
        self.U_best = self.F0

    def __setattr__(self, name: str, value: object) -> None:
        if name == "F0" and hasattr(self, "F0"):
            raise AttributeError("F0 is immutable")
        super(SessionState, self).__setattr__(name, value)

    def __delattr__(self, name: str) -> None:
        if name == "F0":
            raise AttributeError("F0 is immutable")
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

    def update_best_checkpoint(self, failures: FailureSet) -> None:
        self.F = failures
        self.U_best = failures

    @staticmethod
    def _append_recent(events: tuple[Event, ...], event: Event) -> tuple[Event, ...]:
        return (events + (event,))[-RECENT_EVENT_LIMIT:]
