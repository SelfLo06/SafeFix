from safefix.context import ContextBuilder
from safefix.events import SessionEvent
from safefix.artifacts import ArtifactWriter
from safefix.memory import MAX_MEMORY_ENTRIES, ProjectMemoryStore
from safefix.models import (
    BaselineSource,
    Feedback,
    FailureSet,
    GuardDecision,
    Phase,
    ReviewVerdict,
    SessionResult,
    StopReason,
    ToolCall,
    ToolName,
)
from safefix.review import ReviewResult
from safefix.session_state import SessionState
from safefix.test_manifest import FrozenTestManifest, ManifestEntry
from safefix.testprep import PreparationSummary
from safefix.testprep.models import CoverageRequirement


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

    assert context["project_memory"] == [f"summary-{MAX_MEMORY_ENTRIES}"]


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
        {
            "tool": "read_file",
            "outcome": "tool",
            "summary": "read app",
            "labels": {},
        }
    ]
    assert context["recent_guard_feedback"] == [
        {"tool": "read_file", "decision": "deny"}
    ]


def test_context_contains_bounded_guidance_and_safe_review_summary(tmp_path):
    state = SessionState(failures("case-a"))
    state.record_guidance("Authorization: Bearer context-secret " + "x" * 600)
    state.record_event(
        SessionEvent(
            sequence=1,
            timestamp="2026-08-06T00:00:00Z",
            phase=Phase.READY,
            kind="review",
            safe_payload={"summary": "complete source response"},
        )
    )
    state.set_review(
        ReviewResult(
            verdict=ReviewVerdict.WARN,
            basis_supported=True,
            invented_behavior=False,
            implementation_coupling=False,
            risk="medium",
            summary="Authorization: Bearer review-secret",
        )
    )

    context = ContextBuilder(
        ProjectMemoryStore(tmp_path / "project", data_dir=tmp_path / "data")
    ).build(state)
    rendered = str(context)

    assert len(context["guidance_event_summaries"][0]) <= 512
    assert "review_summary" in context
    assert context["review_summary"]["verdict"] == "warn"
    assert "context-secret" not in rendered
    assert "review-secret" not in rendered
    assert "complete source response" not in rendered


def test_context_contains_frozen_test_counts(tmp_path):
    state = SessionState(failures("case-a"))
    manifest = FrozenTestManifest(
        session_id="session-1",
        baseline_source=BaselineSource.MIXED,
        entries=(
            ManifestEntry(
                path="tests/test_existing.py",
                sha256="a" * 64,
                origin=BaselineSource.EXISTING,
            ),
        ),
        stability_runs=3,
        manifest_hash="b" * 64,
    )
    state.set_preparation(
        PreparationSummary(
            baseline_source=BaselineSource.MIXED,
            existing_test_count=2,
            generated_candidate_count=3,
            generated_accepted_count=1,
            baseline_test_count=4,
            coverage_requirements=(
                CoverageRequirement("behavior-1", "lowercase text"),
                CoverageRequirement(
                    "branch-1", "exercise the decision at src/app.py:4", "src/app.py", (5, 7)
                ),
            ),
            covered_requirement_ids=("behavior-1", "branch-1"),
        ),
        manifest,
    )

    context = ContextBuilder(
        ProjectMemoryStore(tmp_path / "project", data_dir=tmp_path / "data")
    ).build(state)

    assert context["test_summary"] == {
        "baseline_mode": "mixed",
        "baseline_test_count": 4,
        "existing_test_count": 2,
        "generated_candidate_count": 3,
        "generated_accepted_count": 1,
        "coverage_requirements": [
            {
                "id": "behavior-1",
                "behavior": "lowercase text",
                "source_path": None,
                "required_lines": [],
            },
            {
                "id": "branch-1",
                "behavior": "exercise the decision at src/app.py:4",
                "source_path": "src/app.py",
                "required_lines": [5, 7],
            },
        ],
        "covered_requirement_ids": ["behavior-1", "branch-1"],
    }


def test_context_never_retains_code_like_review_source_or_url_data(tmp_path):
    state = SessionState(failures("case-a"))
    state.set_review(
        ReviewResult(
            verdict=ReviewVerdict.WARN,
            basis_supported=True,
            invented_behavior=False,
            implementation_coupling=False,
            risk="https://user:pass@example.test/private?query=secret",
            summary="def leaked_source():\n    return 'SOURCESECRET'",
        )
    )

    context = ContextBuilder(
        ProjectMemoryStore(tmp_path / "project", data_dir=tmp_path / "data")
    ).build(state)

    rendered = repr(context)
    assert "SOURCESECRET" not in rendered
    assert "user:pass" not in rendered
    assert "/private" not in rendered
    assert "query=secret" not in rendered


def test_context_sanitizes_unkeyed_secret_code_traceback_and_outcome_values(tmp_path):
    state = SessionState(failures("case-a"))
    call = ToolCall(tool=ToolName.APPLY_PATCH)
    state.record_tool_event(
        call,
        Feedback(
            outcome="Traceback(TOKENSECRET)",
            summary="print(SOURCESECRET)",
            labels={"detail": "API key/TOKENSECRET"},
        ),
    )
    state.record_event(
        SessionEvent(
            sequence=2,
            timestamp="2026-08-06T00:00:00Z",
            phase=Phase.READY,
            kind="tool",
            safe_payload={"unkeyed": "TOKENSECRET"},
        )
    )
    state.record_guidance("Bearer TOKENSECRET")
    state.record_patch_fingerprint("Bearer TOKENSECRET")
    state.set_review(
        ReviewResult(
            verdict=ReviewVerdict.WARN,
            basis_supported=True,
            invented_behavior=False,
            implementation_coupling=False,
            risk="Exception(TOKENSECRET)",
            summary="API key/TOKENSECRET",
        )
    )

    context = ContextBuilder(
        ProjectMemoryStore(tmp_path / "project", data_dir=tmp_path / "data")
    ).build(state)
    rendered = repr(context)

    for secret in ("TOKENSECRET", "SOURCESECRET", "Traceback", "Exception", "print("):
        assert secret not in rendered


def test_shared_sanitizer_redacts_nested_userinfo_and_source_key_names(tmp_path):
    nested_safe_values = {
        "userinfo": "value",
        "source": "value",
        "visible": "safe-value",
    }
    event = SessionEvent(
        sequence=3,
        timestamp="2026-08-06T00:00:00Z",
        phase=Phase.READY,
        kind="tool",
        safe_payload={"nested": nested_safe_values},
    )
    state = SessionState(failures("case-a"))
    state.record_tool_event(
        ToolCall(tool=ToolName.READ_FILE),
        Feedback(outcome="safe", summary="safe", labels={"nested": nested_safe_values}),
    )
    state.set_high_risk_confirmation({"nested": nested_safe_values})
    state.set_preparation(
        PreparationSummary(baseline_source=BaselineSource.EXISTING),
        FrozenTestManifest(
            session_id="session-1",
            baseline_source=BaselineSource.EXISTING,
            entries=(
                ManifestEntry("tests/test_app.py", "hash", BaselineSource.EXISTING),
            ),
            stability_runs=1,
            manifest_hash="manifest-hash",
        ),
    )

    context = ContextBuilder(
        ProjectMemoryStore(tmp_path / "project", data_dir=tmp_path / "data")
    ).build(state)
    artifact_path = tmp_path / "session.json"
    ArtifactWriter(artifact_path).write(
        state, SessionResult(stop_reason=StopReason.ERROR)
    )

    assert "userinfo" not in repr(event)
    assert "source" not in repr(event)
    assert "userinfo" not in repr(context)
    assert "source" not in repr(context)
    artifact = artifact_path.read_text()
    assert '"userinfo":' not in artifact
    assert '"source":' not in artifact
