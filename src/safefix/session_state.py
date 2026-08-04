from dataclasses import dataclass, field
from typing import TypeVar

from .models import Feedback, FailureSet, GuardDecision, ToolCall


RECENT_EVENT_LIMIT = 10
Event = TypeVar("Event")


@dataclass
class SessionState:
    F0: FailureSet
    F: FailureSet = field(init=False)
    U_best: FailureSet = field(init=False)
    steps: int = 0
    rounds: int = 0
    no_progress_rounds: int = 0
    _recent_tool_events: list[tuple[ToolCall, Feedback]] = field(
        default_factory=list, init=False, repr=False
    )
    _recent_guard_events: list[tuple[ToolCall, GuardDecision]] = field(
        default_factory=list, init=False, repr=False
    )
    _patch_fingerprints: set[str] = field(
        default_factory=set, init=False, repr=False
    )

    @property
    def recent_tool_events(self) -> tuple[tuple[ToolCall, Feedback], ...]:
        return tuple(self._recent_tool_events)

    @property
    def recent_guard_events(self) -> tuple[tuple[ToolCall, GuardDecision], ...]:
        return tuple(self._recent_guard_events)

    @property
    def patch_fingerprints(self) -> frozenset[str]:
        return frozenset(self._patch_fingerprints)

    def __post_init__(self) -> None:
        self.F = self.F0
        self.U_best = self.F0

    def __setattr__(self, name: str, value: object) -> None:
        if name == "F0" and "F0" in self.__dict__:
            raise AttributeError("F0 is immutable")
        super().__setattr__(name, value)

    def increment_step(self) -> None:
        self.steps += 1

    def increment_round(self) -> None:
        self.rounds += 1

    def increment_no_progress(self) -> None:
        self.no_progress_rounds += 1

    def reset_no_progress(self) -> None:
        self.no_progress_rounds = 0

    def record_tool_event(self, call: ToolCall, feedback: Feedback) -> None:
        self._append_recent(self._recent_tool_events, (call, feedback))

    def record_guard_event(self, call: ToolCall, decision: GuardDecision) -> None:
        self._append_recent(self._recent_guard_events, (call, decision))

    def record_patch_fingerprint(self, fingerprint: str) -> None:
        self._patch_fingerprints.add(fingerprint)

    def update_best_checkpoint(self, failures: FailureSet) -> None:
        self.F = failures
        self.U_best = failures

    @staticmethod
    def _append_recent(events: list[Event], event: Event) -> None:
        events.append(event)
        if len(events) > RECENT_EVENT_LIMIT:
            del events[0]
