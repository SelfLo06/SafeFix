import pytest

from safefix.events import SessionEvent
from safefix.models import (
    BaselineSource,
    Feedback,
    FailureSet,
    GuardDecision,
    Phase,
    ReviewVerdict,
    ToolCall,
    ToolName,
)
from safefix.review import ReviewResult
from safefix.session_state import RECENT_EVENT_LIMIT, SessionState
from safefix.test_manifest import FrozenTestManifest, ManifestEntry
from safefix.testprep import PreparationSummary


def failures(*ids: str) -> FailureSet:
    return FailureSet(frozenset(ids))


def test_session_state_defaults():
    baseline = failures("case-a", "case-b")

    state = SessionState(baseline)

    assert state.F0 == baseline
    assert state.F == baseline
    assert state.U_best == baseline
    assert state.steps == state.rounds == state.no_progress_rounds == 0
    assert state.recent_tool_events == ()
    assert state.recent_guard_events == ()
    assert state.patch_fingerprints == frozenset()
    with pytest.raises(AttributeError):
        state.F0 = failures("case-a")


def test_session_state_preserves_v01_positional_constructor():
    state = SessionState(failures("case-a"), 1, 2, 3)

    assert state.F0 == failures("case-a")
    assert (state.steps, state.rounds, state.no_progress_rounds) == (1, 2, 3)


def test_session_state_rejects_baseline_deletion_and_reassignment():
    baseline = failures("case-a")
    state = SessionState(baseline)

    with pytest.raises(AttributeError):
        del state.F0

    assert state.F0 == baseline
    with pytest.raises(AttributeError):
        state.F0 = failures("replacement")


def test_session_state_records_tool_and_guard_events():
    state = SessionState(failures("case-a"))
    call = ToolCall(tool=ToolName.READ_FILE, path="src/app.py")
    feedback = Feedback(outcome="tool", summary="read src/app.py")

    state.increment_step()
    state.increment_round()
    state.increment_no_progress()
    for _ in range(RECENT_EVENT_LIMIT + 1):
        state.record_tool_event(call, feedback)
        state.record_guard_event(call, GuardDecision.DENY)

    assert state.steps == state.rounds == state.no_progress_rounds == 1
    assert state.recent_tool_events == ((call, feedback),) * RECENT_EVENT_LIMIT
    assert state.recent_guard_events == (
        (call, GuardDecision.DENY),
    ) * RECENT_EVENT_LIMIT


def test_last_feedback_is_sanitized_at_the_state_boundary():
    state = SessionState(failures("case-a"))

    state.last_feedback = Feedback(
        outcome="Traceback(TOKENSECRET)",
        summary="print(SOURCESECRET)",
    )

    assert state.last_feedback.outcome == "[REDACTED]"
    assert state.last_feedback.summary == "[REDACTED]"


def test_session_state_exposes_bounded_histories_as_read_only():
    state = SessionState(failures("case-a"))
    events = [
        (
            ToolCall(tool=ToolName.READ_FILE, path=f"src/file-{index}.py"),
            Feedback(outcome="tool", summary=f"read file {index}"),
        )
        for index in range(RECENT_EVENT_LIMIT + 1)
    ]

    for call, feedback in events:
        state.record_tool_event(call, feedback)
        state.record_guard_event(call, GuardDecision.DENY)
    state.record_patch_fingerprint("first-patch")

    assert state.recent_tool_events == tuple(events[1:])
    assert state.recent_guard_events == tuple(
        (call, GuardDecision.DENY) for call, _ in events[1:]
    )
    with pytest.raises(AttributeError):
        state.recent_tool_events.append(events[0])
    with pytest.raises(AttributeError):
        state.recent_guard_events.append((events[0][0], GuardDecision.DENY))
    with pytest.raises(AttributeError):
        state.patch_fingerprints.add("second-patch")


def test_session_state_updates_best_checkpoint():
    state = SessionState(failures("case-a", "case-b"))

    state.record_patch_fingerprint("first-patch")
    state.record_patch_fingerprint("first-patch")
    state.update_best_checkpoint(failures("case-a"))

    assert state.F == failures("case-a")
    assert state.U_best == failures("case-a")
    assert state.patch_fingerprints == frozenset({"first-patch"})


def test_preparation_metadata_is_frozen_without_replacing_f0():
    state = SessionState(
        failures("case-a"),
        test_model_identity="test:https://test.example:test-model",
        repair_model_identity="repair:https://repair.example:repair-model",
        review_model_identity="review:https://review.example:review-model",
    )
    summary = PreparationSummary(
        baseline_source=BaselineSource.MIXED,
        existing_test_count=4,
        generated_candidate_count=2,
        generated_accepted_count=1,
        generated_pass_accepted=1,
        rejected_count=1,
    )
    manifest = FrozenTestManifest(
        session_id="session-1",
        baseline_source=BaselineSource.MIXED,
        entries=(ManifestEntry("tests/test_app.py", "hash", BaselineSource.EXISTING),),
        stability_runs=3,
        manifest_hash="manifest-hash",
    )

    state.set_preparation(summary, manifest)

    assert state.preparation_summary is summary
    assert state.baseline_source is BaselineSource.MIXED
    assert state.manifest_hash == "manifest-hash"
    assert state.stability_runs == 3
    with pytest.raises(AttributeError, match="preparation"):
        state.set_preparation(summary, manifest)
    with pytest.raises(AttributeError, match="F0"):
        state.F0 = failures("replacement")


def test_session_state_records_bounded_safe_events_guidance_and_review():
    state = SessionState(failures("case-a"))
    event = SessionEvent(
        sequence=1,
        timestamp="2026-08-06T00:00:00Z",
        phase=Phase.READY,
        kind="guidance",
        safe_payload={"summary": "Authorization: Bearer secret-token"},
    )
    for _ in range(RECENT_EVENT_LIMIT + 1):
        state.record_event(event)
    state.record_guidance("Authorization: Bearer secret-token " + "x" * 600)
    review = ReviewResult(
        verdict=ReviewVerdict.PASS,
        basis_supported=True,
        invented_behavior=False,
        implementation_coupling=False,
        risk="low",
        summary="Authorization: Bearer review-secret",
    )
    state.set_review(review)
    state.set_high_risk_confirmation(
        {"confirmed": True, "source": "tui", "api_key": "secret-token"}
    )

    assert len(state.recent_events) == RECENT_EVENT_LIMIT
    assert "secret-token" not in state.guidance_event_summaries[0]
    assert state.review_result is not review
    assert state.high_risk_confirmation["confirmed"] is True
    assert "[REDACTED]" in state.high_risk_confirmation.values()


def test_high_risk_confirmation_is_not_mutable_through_nested_values():
    state = SessionState(failures("case-a"))
    state.set_high_risk_confirmation(
        {"confirmed": True, "details": {"operator": "human"}}
    )

    exposed = state.high_risk_confirmation
    exposed["details"]["operator"] = "changed"

    assert state.high_risk_confirmation == {
        "confirmed": True,
        "details": {"operator": "human"},
    }


def test_high_risk_confirmation_cannot_be_deleted_reset_or_mutated_after_set():
    state = SessionState(failures("case-a"))
    state.set_high_risk_confirmation(
        {"confirmed": True, "details": {"operator": "human"}}
    )

    with pytest.raises(AttributeError):
        del state._high_risk_confirmation
    with pytest.raises(AttributeError):
        state._high_risk_confirmation = None
    with pytest.raises(TypeError):
        state._high_risk_confirmation["confirmed"] = False
    with pytest.raises(AttributeError):
        state.set_high_risk_confirmation({"confirmed": False})

    assert state.high_risk_confirmation["confirmed"] is True


def test_review_is_sanitized_before_storage_and_repr():
    state = SessionState(
        failures("case-a"),
        repair_model_identity=(
            "repair:https://user:pass@example.test/private?user=alice:repair-model"
        ),
    )
    review = ReviewResult(
        verdict=ReviewVerdict.PASS,
        basis_supported=True,
        invented_behavior=False,
        implementation_coupling=False,
        risk="low",
        summary="def secret_source():\n    return 'TOPSECRET' raw model response",
    )

    state.set_review(review)

    assert state.review_result is not review
    assert state.review_result.summary == "[REDACTED]"
    assert state.repair_model_identity == "repair:https://example.test:repair-model"
    assert "TOPSECRET" not in repr(state)
    assert "user:pass" not in repr(state)
    assert "/private" not in repr(state)
    assert "user=alice" not in repr(state)


def test_direct_metadata_assignment_is_rejected_with_boundary_error():
    state = SessionState(failures("case-a"))

    with pytest.raises(ValueError, match="session metadata"):
        state.preparation_summary = {"generated_candidate_count": object()}
    with pytest.raises(ValueError, match="session metadata"):
        state.baseline_source = "not-a-baseline"
    with pytest.raises(ValueError, match="session metadata"):
        state.manifest_hash = object()
    with pytest.raises(ValueError, match="session metadata"):
        state.stability_runs = "3"
    with pytest.raises(ValueError, match="session metadata"):
        state.review_result = {"summary": "raw"}


def test_set_metadata_is_immutable_after_valid_assignment():
    state = SessionState(failures("case-a"))
    summary = PreparationSummary(baseline_source=BaselineSource.EXISTING)
    manifest = FrozenTestManifest(
        session_id="session-1",
        baseline_source=BaselineSource.EXISTING,
        entries=(ManifestEntry("tests/test_app.py", "hash", BaselineSource.EXISTING),),
        stability_runs=1,
        manifest_hash="manifest-hash",
    )
    state.set_preparation(summary, manifest)

    with pytest.raises(AttributeError):
        state.preparation_summary = summary
    with pytest.raises(AttributeError):
        state.baseline_source = BaselineSource.MIXED
    with pytest.raises(AttributeError):
        state.manifest_hash = "replacement"
    with pytest.raises(AttributeError):
        state.stability_runs = 2


def test_metadata_setters_reject_malformed_review_guidance_and_confirmation():
    state = SessionState(failures("case-a"))

    with pytest.raises(ValueError, match="session metadata"):
        state.record_guidance(object())
    with pytest.raises(ValueError, match="session metadata"):
        state.set_review(
            ReviewResult(
                verdict=ReviewVerdict.PASS,
                basis_supported=True,
                invented_behavior=False,
                implementation_coupling=False,
                risk="low",
                summary=object(),  # type: ignore[arg-type]
            )
        )
    with pytest.raises(ValueError, match="session metadata"):
        state.set_high_risk_confirmation({"confirmed": "yes"})  # type: ignore[arg-type]
