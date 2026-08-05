"""Bounded, source-free repair context."""

from .memory import ProjectMemoryStore
from .session_state import SessionState


MAX_CONTEXT_FAILURES = 20


class ContextBuilder:
    """Build the structured context supplied to a repair request."""

    def __init__(self, memory_store: ProjectMemoryStore) -> None:
        self._memory_store = memory_store

    def build(self, state: SessionState, *, use_memory: bool = False) -> dict[str, object]:
        current_failures = sorted(state.F.ids)[:MAX_CONTEXT_FAILURES]
        best_failures = sorted(state.U_best.ids)[:MAX_CONTEXT_FAILURES]
        context: dict[str, object] = {
            "current_failures": current_failures,
            "best_summary": {
                "failure_count": len(state.U_best.ids),
                "failure_ids": best_failures,
            },
            "recent_tool_feedback": [
                {
                    "tool": call.tool.value,
                    "outcome": feedback.outcome,
                    "summary": feedback.summary,
                    "labels": feedback.labels,
                }
                for call, feedback in state.recent_tool_events
            ],
            "recent_guard_feedback": [
                {"tool": call.tool.value, "decision": decision.value}
                for call, decision in state.recent_guard_events
            ],
        }
        if use_memory:
            context["project_memory"] = list(self._memory_store.load(use_memory=True))
            context["project_memory_fingerprints"] = list(
                self._memory_store.load_fingerprints(use_memory=True)
            )
        return context
