import pytest

from safefix.models import Feedback, FailureSet, GuardDecision, ToolCall, ToolName
from safefix.session_state import RECENT_EVENT_LIMIT, SessionState


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
