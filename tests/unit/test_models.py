from safefix.models import (
    AcceptanceMode,
    BaselineSource,
    CandidateStatus,
    ModelRole,
    ModelRoleConfig,
    Phase,
    ReviewVerdict,
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
        "OPERATOR_STOP",
        "TEST_PREPARATION_ERROR",
        "FINAL_REVIEW_REJECTED",
    }


def test_v02_enum_values_are_stable():
    assert {item.value for item in ModelRole} == {"test", "repair", "review"}
    assert {item.value for item in BaselineSource} == {"existing", "generated", "mixed"}
    assert {item.value for item in AcceptanceMode} == {"review", "standard", "high-risk"}
    assert {item.value for item in CandidateStatus} == {"pass", "fail", "error", "flaky"}
    assert {item.value for item in ReviewVerdict} == {
        "pass",
        "warn",
        "review_required",
        "not_configured",
    }
    assert Phase.PROJECT_INTAKE.value == "project_intake"
    assert Phase.FINAL_REVIEW_GATE.value == "final_review_gate"


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
    assert config.generate_tests is False
    assert config.baseline_source is BaselineSource.EXISTING
    assert config.acceptance_mode is AcceptanceMode.STANDARD
    assert config.stability_runs == 3
    assert config.max_auto_accepted_failures == 3


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
    assert exit_code_for_stop_reason(StopReason.OPERATOR_STOP) == 1
    assert exit_code_for_stop_reason(StopReason.FINAL_REVIEW_REJECTED) == 1
    assert exit_code_for_stop_reason(StopReason.TEST_PREPARATION_ERROR) == 3


def test_session_result_derives_exit_code_from_stop_reason():
    assert SessionResult(stop_reason=StopReason.ERROR).exit_code == 3


def test_model_role_config_is_frozen_and_fingerprint_is_redacted():
    config = ModelRoleConfig(
        role=ModelRole.REPAIR,
        base_url="https://llm.example/v1?api_key=secret",
        model="repair-model",
        keyring_service="safefix-repair",
    )

    assert config.identity_fingerprint == "repair:https://llm.example:repair-model"
    assert "secret" not in config.identity_fingerprint

    try:
        config.model = "other-model"
    except (AttributeError, TypeError):
        pass
    else:
        raise AssertionError("ModelRoleConfig must be immutable")
