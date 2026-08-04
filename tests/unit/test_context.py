from safefix.context import ContextBuilder
from safefix.memory import MAX_MEMORY_ENTRIES, ProjectMemoryStore
from safefix.models import Feedback, FailureSet, GuardDecision, ToolCall, ToolName
from safefix.session_state import SessionState


def failures(*ids: str) -> FailureSet:
    return FailureSet(frozenset(ids))


def test_context_without_memory_has_no_project_slice(tmp_path):
    store = ProjectMemoryStore(tmp_path / "project", data_dir=tmp_path / "data")
    store.update("previous public repair summary")

    context = ContextBuilder(store).build(SessionState(failures("case-a")))

    assert "project_memory" not in context
    assert context["current_failures"] == ["case-a"]


def test_context_with_memory_includes_capped_slice(tmp_path):
    store = ProjectMemoryStore(tmp_path / "project", data_dir=tmp_path / "data")
    for index in range(MAX_MEMORY_ENTRIES + 1):
        store.update(f"summary-{index}")

    context = ContextBuilder(store).build(
        SessionState(failures("case-a")), use_memory=True
    )

    assert context["project_memory"] == [
        f"summary-{index}" for index in range(1, MAX_MEMORY_ENTRIES + 1)
    ]


def test_context_contains_failure_and_tool_feedback(tmp_path):
    state = SessionState(failures("case-b", "case-a"))
    state.update_best_checkpoint(failures("case-a"))
    call = ToolCall(tool=ToolName.READ_FILE, path="src/app.py")
    state.record_tool_event(call, Feedback(outcome="tool", summary="read app"))
    state.record_guard_event(call, GuardDecision.DENY)

    context = ContextBuilder(
        ProjectMemoryStore(tmp_path / "project", data_dir=tmp_path / "data")
    ).build(state)

    assert context["current_failures"] == ["case-a"]
    assert context["best_summary"] == {"failure_count": 1, "failure_ids": ["case-a"]}
    assert context["recent_tool_feedback"] == [
        {"tool": "read_file", "outcome": "tool"}
    ]
    assert context["recent_guard_feedback"] == [
        {"tool": "read_file", "decision": "deny"}
    ]
