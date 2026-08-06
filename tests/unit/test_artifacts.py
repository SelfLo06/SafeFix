import json

import pytest

from safefix.artifacts import ArtifactWriter
from safefix.models import (
    BaselineSource,
    Change,
    FailureSet,
    Feedback,
    GuardDecision,
    SessionResult,
    ReviewVerdict,
    StopReason,
    ToolCall,
    ToolName,
)
from safefix.review import ReviewResult
from safefix.session_state import SessionState
from safefix.test_manifest import FrozenTestManifest, ManifestEntry
from safefix.testprep import PreparationSummary


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
    state.record_tool_event(
        ToolCall(tool=ToolName.APPLY_PATCH), Feedback("error")
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
    assert artifact["failure_sets"] == {
        "baseline": ["test_app.py::test_fixed", "test_app.py::test_old"],
        "current": ["test_app.py::test_new", "test_app.py::test_old"],
        "unresolved_best": ["test_app.py::test_fixed", "test_app.py::test_old"],
    }
    assert artifact["unresolved_current"] == ["test_app.py::test_old"]
    assert artifact["new_failures"] == ["test_app.py::test_new"]
    assert artifact["best_summary"] == {
        "failure_count": 2,
        "failure_ids": ["test_app.py::test_fixed", "test_app.py::test_old"],
    }
    assert artifact["exit_code"] == 1
    assert artifact["tool_events"] == [
        {"tool": "apply_patch", "outcome": "error"}
    ]
    assert artifact["patch_fingerprints"] == []


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


def test_artifact_preserves_last_evaluation_after_best_restore(tmp_path):
    state = SessionState(failures("baseline"))
    state.F = failures("baseline", "new-failure")
    state.U_best = failures("baseline")
    state.last_evaluated = state.F

    ArtifactWriter(tmp_path / "artifact.json").write(
        state, SessionResult(stop_reason=StopReason.NO_PROGRESS)
    )

    artifact = json.loads((tmp_path / "artifact.json").read_text())
    assert artifact["failure_sets"]["current"] == ["baseline", "new-failure"]
    assert artifact["new_failures"] == ["new-failure"]


def test_v2_artifact_contains_preparation_metadata_without_raw_values(tmp_path):
    state = SessionState(
        failures("case-a"),
        test_model_identity="test:https://test.example:test-model",
        repair_model_identity="repair:https://repair.example:repair-model",
        review_model_identity="review:https://review.example:review-model",
    )
    state.set_preparation(
        PreparationSummary(
            baseline_source=BaselineSource.MIXED,
            existing_test_count=3,
            generated_candidate_count=2,
            generated_accepted_count=1,
            generated_pass_accepted=1,
            generated_fail_accepted_manual=0,
            generated_fail_accepted_automatic=0,
            rejected_count=1,
            error_count=0,
            flaky_count=0,
        ),
        FrozenTestManifest(
            session_id="session-1",
            baseline_source=BaselineSource.MIXED,
            entries=(ManifestEntry("tests/test_app.py", "hash", BaselineSource.EXISTING),),
            stability_runs=3,
            manifest_hash="manifest-hash",
        ),
    )
    state.record_guidance("Authorization: Bearer artifact-secret")
    state.set_high_risk_confirmation(
        {"confirmed": True, "source": "tui", "api_key": "artifact-secret"}
    )
    state.set_review(
        ReviewResult(
            verdict=ReviewVerdict.PASS,
            basis_supported=True,
            invented_behavior=False,
            implementation_coupling=False,
            risk="low",
            summary="full source response Authorization: Bearer artifact-secret",
        )
    )

    ArtifactWriter(tmp_path / "session.json").write(
        state, SessionResult(stop_reason=StopReason.SUCCESS)
    )
    payload = json.loads((tmp_path / "session.json").read_text())
    rendered = json.dumps(payload)

    assert payload["baseline_source"] == "mixed"
    assert payload["existing_test_count"] == 3
    assert payload["generated_candidate_count"] == 2
    assert payload["generated_pass_accepted"] == 1
    assert payload["baseline_manifest_hash"] == "manifest-hash"
    assert payload["stability_runs"] == 3
    assert payload["test_model_identity"] == "test:https://test.example:test-model"
    assert payload["review_verdict"] == "pass"
    assert payload["guidance_event_summaries"] == ["[REDACTED]"]
    assert "Authorization" not in rendered
    assert "[REDACTED]" in payload["high_risk_confirmation"].values()
    assert "artifact-secret" not in rendered
    assert "full source response" not in rendered


def test_artifact_rejects_malformed_preparation_state_instead_of_inventing_counts(tmp_path):
    state = SessionState(failures("case-a"))
    object.__setattr__(
        state,
        "preparation_summary",
        {"generated_candidate_count": object()},
    )

    with pytest.raises(ValueError, match="session metadata"):
        ArtifactWriter(tmp_path / "session.json").write(
            state, SessionResult(stop_reason=StopReason.ERROR)
        )
