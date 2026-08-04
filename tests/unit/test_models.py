from safefix.models import (
    StopReason,
    GuardDecision,
    ToolName,
    Change,
    ToolCall,
    FailureSet,
    Feedback,
    Config,
    SessionResult,
)


def test_stop_reason_values():
    assert {item.name for item in StopReason} == {
        "SUCCESS",
        "REQUESTED",
        "MAX_STEPS",
        "MAX_ROUNDS",
        "NO_PROGRESS",
        "ERROR",
        "CONFIG_ERROR",
    }


def test_tool_call_apply_patch_roundtrip():
    change = Change("src/app.py", "return 1", "return 2")
    call = ToolCall(tool=ToolName.APPLY_PATCH, changes=(change,))
    assert call.changes[0] == change


def test_failure_set_ids():
    failures = FailureSet(frozenset({"case-a", "case-b"}))
    assert failures.ids == frozenset({"case-a", "case-b"})


def test_config_defaults():
    config = Config()
    assert config.max_steps == 30
    assert config.max_rounds == 10
    assert config.max_no_progress_rounds == 3


def test_config_fields_exist():
    config = Config()
    assert hasattr(config, "allowed_paths")
    assert hasattr(config, "excluded_paths")
    assert hasattr(config, "pytest_args")
    assert hasattr(config, "base_url")
    assert hasattr(config, "model")
