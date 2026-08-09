"""Redacted JSON artifacts for completed SafeFix sessions."""

from dataclasses import replace
from enum import Enum
import json
import os
from pathlib import Path
import tempfile

from .models import AcceptanceMode, BaselineSource, ReviewVerdict, SessionResult
from .review import ReviewResult
from .events import sanitize_untrusted
from .session_state import SessionState, SessionStateBoundaryError, safe_summary
from .testprep.service import PreparationSummary


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
        _validate_metadata(state)
        current = state.last_evaluated or state.F
        preparation = state.preparation_summary
        review = state.review_result
        payload = {
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
                preparation, "existing_test_count"
            ),
            "generated_candidate_count": _preparation_value(
                preparation, "generated_candidate_count"
            ),
            "generated_accepted_count": _preparation_value(
                preparation, "generated_accepted_count"
            ),
            "generated_pass_accepted": _preparation_value(
                preparation, "generated_pass_accepted"
            ),
            "generated_fail_accepted_manual": _preparation_value(
                preparation, "generated_fail_accepted_manual"
            ),
            "generated_fail_accepted_automatic": _preparation_value(
                preparation, "generated_fail_accepted_automatic"
            ),
            "rejected_count": _preparation_value(preparation, "rejected_count"),
            "error_count": _preparation_value(preparation, "error_count"),
            "flaky_count": _preparation_value(preparation, "flaky_count"),
            "acceptance_mode": _enum_value(state.acceptance_mode),
            "stability_runs": state.stability_runs,
            "test_model_identity": _safe_identity(state.test_model_identity),
            "repair_model_identity": _safe_identity(state.repair_model_identity),
            "review_model_identity": _safe_identity(state.review_model_identity),
            "review_verdict": _enum_value(review.verdict) if review else None,
            "review_summary": _safe_review_text(review.summary) if review else None,
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
        sanitized = sanitize_untrusted(payload)
        if not isinstance(sanitized, dict):
            raise SessionStateBoundaryError("invalid session artifact payload")
        # These records are already validated at their typed boundaries.  Add
        # them after generic sanitization so semantic names such as "response"
        # are not mistaken for untrusted transport payloads.
        sanitized["explanations"] = [
            {"question": question, "response": response}
            for question, response in state.explanation_records
        ]
        sanitized["coverage_requirements"] = (
            [
                {
                    "id": item.requirement_id,
                    "behavior": safe_summary(item.behavior),
                    "source_path": item.source_path,
                    "required_lines": list(item.required_lines),
                }
                for item in preparation.coverage_requirements
            ]
            if preparation is not None
            else None
        )
        sanitized["covered_requirement_ids"] = (
            list(preparation.covered_requirement_ids) if preparation is not None else None
        )
        sanitized["semantic_events"] = [
            {
                "sequence": event.sequence,
                "timestamp": event.timestamp,
                "phase": event.phase.value,
                "kind": event.kind,
                "payload": event.safe_payload,
            }
            for event in state.recent_events
        ]
        return sanitized


def _preparation_value(preparation: PreparationSummary | None, name: str) -> object:
    if preparation is None:
        return None
    if not isinstance(preparation, PreparationSummary):
        raise SessionStateBoundaryError("invalid session metadata: preparation_summary")
    return getattr(preparation, name)


def _enum_value(value: object | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, Enum) or not isinstance(value.value, str):
        raise SessionStateBoundaryError("invalid session metadata: enum value")
    return value.value


def _safe_identity(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SessionStateBoundaryError("invalid session metadata: model identity")
    from .events import sanitize_model_identity

    try:
        return sanitize_model_identity(value)
    except (TypeError, ValueError) as exc:
        raise SessionStateBoundaryError("invalid session metadata: model identity") from exc


def _review_payload(review: object | None) -> dict[str, object] | None:
    if review is None:
        return None
    if not isinstance(review, ReviewResult):
        raise SessionStateBoundaryError("invalid session metadata: review_result")
    return {
        "verdict": _enum_value(review.verdict),
        "warning": review.verdict in {ReviewVerdict.WARN, ReviewVerdict.REVIEW_REQUIRED},
        "basis_supported": review.basis_supported,
        "invented_behavior": review.invented_behavior,
        "implementation_coupling": review.implementation_coupling,
        "risk": _safe_review_text(review.risk),
        "summary": _safe_review_text(review.summary),
    }


def _safe_review_text(value: object) -> str:
    if not isinstance(value, str):
        raise SessionStateBoundaryError("invalid session metadata: review_result")
    try:
        return safe_summary(value)
    except (TypeError, ValueError) as exc:
        raise SessionStateBoundaryError("invalid session metadata: review_result") from exc


def _validate_metadata(state: SessionState) -> None:
    preparation = state.preparation_summary
    if preparation is not None:
        if not isinstance(preparation, PreparationSummary):
            raise SessionStateBoundaryError("invalid session metadata: preparation_summary")
        for name in (
            "existing_test_count",
            "baseline_test_count",
            "generated_candidate_count",
            "generated_accepted_count",
            "generated_pass_accepted",
            "generated_fail_accepted_manual",
            "generated_fail_accepted_automatic",
            "rejected_count",
            "error_count",
            "flaky_count",
        ):
            value = getattr(preparation, name)
            if type(value) is not int or value < 0:
                raise SessionStateBoundaryError(
                    f"invalid session metadata: preparation_summary.{name}"
                )
    if state.baseline_source is not None and not isinstance(state.baseline_source, BaselineSource):
        raise SessionStateBoundaryError("invalid session metadata: baseline_source")
    if state.manifest_hash is not None and not isinstance(state.manifest_hash, str):
        raise SessionStateBoundaryError("invalid session metadata: manifest_hash")
    if state.stability_runs is not None and (
        type(state.stability_runs) is not int or state.stability_runs <= 0
    ):
        raise SessionStateBoundaryError("invalid session metadata: stability_runs")
    for identity in (
        state.test_model_identity,
        state.repair_model_identity,
        state.review_model_identity,
    ):
        if identity is not None and not isinstance(identity, str):
            raise SessionStateBoundaryError("invalid session metadata: model identity")
    if state.acceptance_mode is not None and not isinstance(state.acceptance_mode, AcceptanceMode):
        raise SessionStateBoundaryError("invalid session metadata: acceptance_mode")
    if state.repair_required is not None and type(state.repair_required) is not bool:
        raise SessionStateBoundaryError("invalid session metadata: repair_required")
