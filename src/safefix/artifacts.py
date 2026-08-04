"""Redacted JSON artifacts for completed SafeFix sessions."""

from dataclasses import replace
import json
from pathlib import Path

from .models import SessionResult
from .session_state import SessionState


class ArtifactWriter:
    """Write a human-readable, safe summary for a stopped session."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def write(self, state: SessionState, result: SessionResult) -> SessionResult:
        self._path.write_text(
            json.dumps(self._payload(state, result), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return replace(result, artifact_path=str(self._path))

    @staticmethod
    def _payload(state: SessionState, result: SessionResult) -> dict[str, object]:
        return {
            "counters": {
                "steps": state.steps,
                "rounds": state.rounds,
                "no_progress": state.no_progress_rounds,
            },
            "failure_diffs": {
                "introduced": sorted(state.F.ids - state.F0.ids),
                "resolved": sorted(state.F0.ids - state.F.ids),
            },
            "guard_events": [
                {"tool": call.tool.value, "decision": decision.value}
                for call, decision in state.recent_guard_events
            ],
            "stop_reason": result.stop_reason.value,
        }
