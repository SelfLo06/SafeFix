"""Redacted JSON artifacts for completed SafeFix sessions."""

from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile

from .models import SessionResult
from .session_state import SessionState, safe_summary


class ArtifactWriter:
    """Write a human-readable, safe summary for a stopped session."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def write(self, state: SessionState, result: SessionResult) -> SessionResult:
        rendered = json.dumps(self._payload(state, result), indent=2, sort_keys=True) + "\n"
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(rendered)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, self._path)
        except OSError:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise
        return replace(result, artifact_path=str(self._path))

    @staticmethod
    def _payload(state: SessionState, result: SessionResult) -> dict[str, object]:
        current = state.last_evaluated or state.F
        preparation = state.preparation_summary
        review = state.review_result
        return {
            "counters": {
                "steps": state.steps,
                "rounds": state.rounds,
                "no_progress": state.no_progress_rounds,
            },
            "failure_diffs": {
                "introduced": sorted(current.ids - state.F0.ids),
                "resolved": sorted(state.F0.ids - current.ids),
            },
            "failure_sets": {
                "baseline": sorted(state.F0.ids),
                "current": sorted(current.ids),
                "unresolved_best": sorted(state.U_best.ids),
            },
            "unresolved_current": sorted(current.ids & state.F0.ids),
            "new_failures": sorted(current.ids - state.F0.ids),
            "best_summary": {
                "failure_count": len(state.U_best.ids),
                "failure_ids": sorted(state.U_best.ids),
            },
            "guard_events": [
                {"tool": call.tool.value, "decision": decision.value}
                for call, decision in state.recent_guard_events
            ],
            "tool_events": [
                {"tool": call.tool.value, "outcome": feedback.outcome}
                for call, feedback in state.recent_tool_events
            ],
            "patch_fingerprints": sorted(state.patch_fingerprints),
            "stop_reason": result.stop_reason.value,
            "exit_code": result.exit_code,
            "baseline_source": _enum_value(state.baseline_source),
            "existing_test_count": _preparation_value(
                preparation, "existing_test_count", 0
            ),
            "generated_candidate_count": _preparation_value(
                preparation, "generated_candidate_count", 0
            ),
            "generated_accepted_count": _preparation_value(
                preparation, "generated_accepted_count", 0
            ),
            "generated_pass_accepted": _preparation_value(
                preparation, "generated_pass_accepted", 0
            ),
            "generated_fail_accepted_manual": _preparation_value(
                preparation, "generated_fail_accepted_manual", 0
            ),
            "generated_fail_accepted_automatic": _preparation_value(
                preparation, "generated_fail_accepted_automatic", 0
            ),
            "rejected_count": _preparation_value(preparation, "rejected_count", 0),
            "error_count": _preparation_value(preparation, "error_count", 0),
            "flaky_count": _preparation_value(preparation, "flaky_count", 0),
            "acceptance_mode": _enum_value(state.acceptance_mode),
            "stability_runs": state.stability_runs,
            "test_model_identity": _safe_identity(state.test_model_identity),
            "repair_model_identity": _safe_identity(state.repair_model_identity),
            "review_model_identity": _safe_identity(state.review_model_identity),
            "review_verdict": _enum_value(review.verdict) if review else None,
            "review_summary": safe_summary(review.summary) if review else None,
            "review": _review_payload(review),
            "guidance_event_summaries": list(state.guidance_event_summaries),
            "high_risk_confirmation": state.high_risk_confirmation,
            "baseline_manifest_hash": state.manifest_hash,
            "repair_required": (
                state.repair_required
                if state.repair_required is not None
                else bool(state.F0.ids)
            ),
        }


def _preparation_value(preparation: object | None, name: str, default: object) -> object:
    return getattr(preparation, name, default) if preparation is not None else default


def _enum_value(value: object | None) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", value)


def _safe_identity(value: object) -> str | None:
    if value is None:
        return None
    return safe_summary(value)


def _review_payload(review: object | None) -> dict[str, object] | None:
    if review is None:
        return None
    return {
        "verdict": _enum_value(review.verdict),
        "basis_supported": review.basis_supported,
        "invented_behavior": review.invented_behavior,
        "implementation_coupling": review.implementation_coupling,
        "risk": safe_summary(review.risk),
        "summary": safe_summary(review.summary),
    }
