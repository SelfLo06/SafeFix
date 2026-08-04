import json

from safefix.artifacts import ArtifactWriter
from safefix.models import (
    Change,
    FailureSet,
    Feedback,
    GuardDecision,
    SessionResult,
    StopReason,
    ToolCall,
    ToolName,
)
from safefix.session_state import SessionState


def failures(*ids: str) -> FailureSet:
    return FailureSet(frozenset(ids))


def test_artifact_contains_counters_and_failure_diffs(tmp_path):
    state = SessionState(failures("test_app.py::test_old", "test_app.py::test_fixed"))
    state.increment_step()
    state.increment_step()
    state.increment_round()
    state.increment_no_progress()
    state.F = failures("test_app.py::test_old", "test_app.py::test_new")
    state.record_guard_event(
        ToolCall(tool=ToolName.APPLY_PATCH, changes=(Change("src/app.py", "old", "new"),)),
        GuardDecision.DENY,
    )

    ArtifactWriter(tmp_path / "artifact.json").write(
        state, SessionResult(stop_reason=StopReason.NO_PROGRESS)
    )

    artifact = json.loads((tmp_path / "artifact.json").read_text())
    assert artifact["stop_reason"] == "no_progress"
    assert artifact["counters"] == {"steps": 2, "rounds": 1, "no_progress": 1}
    assert artifact["failure_diffs"] == {
        "introduced": ["test_app.py::test_new"],
        "resolved": ["test_app.py::test_fixed"],
    }
    assert artifact["guard_events"] == [{"tool": "apply_patch", "decision": "deny"}]


def test_artifact_redacts_secret_values(tmp_path):
    state = SessionState(failures("case-a"))
    state.record_tool_event(
        ToolCall(
            tool=ToolName.APPLY_PATCH,
            changes=(Change("src/app.py", "api_key=super-secret", "token=super-secret"),),
            reason="transcript: super-secret",
        ),
        Feedback(
            outcome="error",
            summary="Traceback (most recent call last): super-secret",
            labels={"api_key": "super-secret", "source": "super-secret"},
        ),
    )

    ArtifactWriter(tmp_path / "artifact.json").write(
        state, SessionResult(stop_reason=StopReason.ERROR)
    )

    rendered = (tmp_path / "artifact.json").read_text()
    assert "super-secret" not in rendered
    assert "api_key" not in rendered
    assert "Traceback" not in rendered
    assert "transcript" not in rendered
    assert "old_text" not in rendered
    assert "new_text" not in rendered


def test_artifact_written_for_stop_result(tmp_path):
    path = tmp_path / "artifact.json"
    stop_result = SessionResult(stop_reason=StopReason.MAX_STEPS)

    written_result = ArtifactWriter(path).write(SessionState(failures("case-a")), stop_result)

    assert path.exists()
    assert written_result.stop_reason is StopReason.MAX_STEPS
    assert written_result.artifact_path == str(path)
