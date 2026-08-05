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
    exit_code_for_stop_reason,
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


def test_stop_reason_exit_code_contract():
    assert exit_code_for_stop_reason(StopReason.SUCCESS) == 0
    for reason in (
        StopReason.REQUESTED,
        StopReason.MAX_STEPS,
        StopReason.MAX_ROUNDS,
        StopReason.NO_PROGRESS,
    ):
        assert exit_code_for_stop_reason(reason) == 1
    assert exit_code_for_stop_reason(StopReason.CONFIG_ERROR) == 2
    assert exit_code_for_stop_reason(StopReason.ERROR) == 3


def test_session_result_derives_exit_code_from_stop_reason():
    assert SessionResult(stop_reason=StopReason.ERROR).exit_code == 3
