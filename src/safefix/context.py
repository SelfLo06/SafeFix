"""Bounded, source-free repair context."""

from .events import sanitize_untrusted
from .memory import ProjectMemoryStore
from .session_state import SessionState, safe_summary


MAX_CONTEXT_FAILURES = 20


class ContextBuilder:
    """Build the structured context supplied to a repair request."""

    def __init__(self, memory_store: ProjectMemoryStore) -> None:
        self._memory_store = memory_store

    def build(self, state: SessionState, *, use_memory: bool = False) -> dict[str, object]:
        current_failures = [
            safe_summary(failure_id)
            for failure_id in sorted(state.F.ids & state.F0.ids)[:MAX_CONTEXT_FAILURES]
        ]
        best_failures = [
            safe_summary(failure_id)
            for failure_id in sorted(state.U_best.ids)[:MAX_CONTEXT_FAILURES]
        ]
        context: dict[str, object] = {
            "baseline_failures": [
                safe_summary(failure_id)
                for failure_id in sorted(state.F0.ids)[:MAX_CONTEXT_FAILURES]
            ],
            "current_failures": current_failures,
            "best_summary": {
                "failure_count": len(state.U_best.ids),
                "failure_ids": best_failures,
            },
            "recent_tool_feedback": [
                {
                    "tool": call.tool.value,
                    "outcome": safe_summary(feedback.outcome),
                    "summary": safe_summary(feedback.summary),
                    "labels": sanitize_untrusted(feedback.labels),
                }
                for call, feedback in state.recent_tool_events
            ],
            "recent_guard_feedback": [
                {"tool": call.tool.value, "decision": decision.value}
                for call, decision in state.recent_guard_events
            ],
            "guidance_event_summaries": [
                safe_summary(summary) for summary in state.guidance_event_summaries
            ],
        }
        if state.manifest_hash is not None:
            context["frozen_manifest_hash"] = safe_summary(state.manifest_hash)
        if state.preparation_summary is not None:
            preparation = state.preparation_summary
            context["test_summary"] = {
                "baseline_mode": preparation.baseline_source.value,
                "baseline_test_count": preparation.baseline_test_count,
                "existing_test_count": preparation.existing_test_count,
                "generated_candidate_count": preparation.generated_candidate_count,
                "generated_accepted_count": preparation.generated_accepted_count,
                "coverage_requirements": [
                    {
                        "id": item.requirement_id,
                        "behavior": safe_summary(item.behavior),
                        "source_path": item.source_path,
                        "required_lines": list(item.required_lines),
                    }
                    for item in preparation.coverage_requirements
                ],
                "covered_requirement_ids": list(preparation.covered_requirement_ids),
            }
        if state.review_result is not None:
            review = state.review_result
            context["review_summary"] = {
                "verdict": getattr(review.verdict, "value", review.verdict),
                "risk": safe_summary(review.risk),
                "summary": safe_summary(review.summary),
            }
        if use_memory:
            context["project_memory"] = [
                safe_summary(summary)
                for summary in self._memory_store.load(use_memory=True)
            ]
            context["project_memory_fingerprints"] = list(
                safe_summary(fingerprint)
                for fingerprint in self._memory_store.load_fingerprints(use_memory=True)
            )
        return context
