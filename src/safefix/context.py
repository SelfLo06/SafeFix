"""Bounded, source-free repair context."""

from .memory import ProjectMemoryStore
from .session_state import SessionState, safe_summary


MAX_CONTEXT_FAILURES = 20


class ContextBuilder:
    """Build the structured context supplied to a repair request."""

    def __init__(self, memory_store: ProjectMemoryStore) -> None:
        self._memory_store = memory_store

    def build(self, state: SessionState, *, use_memory: bool = False) -> dict[str, object]:
        current_failures = sorted(state.F.ids & state.F0.ids)[:MAX_CONTEXT_FAILURES]
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
                    "summary": safe_summary(feedback.summary),
                    "labels": {
                        safe_summary(key): safe_summary(value)
                        for key, value in feedback.labels.items()
                    },
                }
                for call, feedback in state.recent_tool_events
            ],
            "recent_guard_feedback": [
                {"tool": call.tool.value, "decision": decision.value}
                for call, decision in state.recent_guard_events
            ],
            "guidance_event_summaries": list(state.guidance_event_summaries),
        }
        if state.review_result is not None:
            review = state.review_result
            context["review_summary"] = {
                "verdict": getattr(review.verdict, "value", review.verdict),
                "risk": safe_summary(review.risk),
                "summary": safe_summary(review.summary),
            }
        if use_memory:
            context["project_memory"] = list(self._memory_store.load(use_memory=True))
            context["project_memory_fingerprints"] = list(
                self._memory_store.load_fingerprints(use_memory=True)
            )
        return context
