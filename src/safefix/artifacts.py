"""Redacted JSON artifacts for completed SafeFix sessions."""

from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile

from .models import SessionResult
from .session_state import SessionState


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
        }
