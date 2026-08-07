from dataclasses import dataclass, field
from enum import Enum


class ModelRole(str, Enum):
    TEST = "test"
    REPAIR = "repair"
    REVIEW = "review"


_ROLE_SERVICES = {
    ModelRole.TEST: "safefix-test",
    ModelRole.REPAIR: "safefix",
    ModelRole.REVIEW: "safefix-review",
}


def role_service_name(role: ModelRole) -> str:
    """Return the fixed keyring service for a model role."""
    return _ROLE_SERVICES[ModelRole(role)]


class BaselineSource(str, Enum):
    EXISTING = "existing"
    GENERATED = "generated"
    MIXED = "mixed"


class AcceptanceMode(str, Enum):
    REVIEW = "review"
    STANDARD = "standard"
    HIGH_RISK = "high-risk"


class CandidateStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    FLAKY = "flaky"


class ReviewVerdict(str, Enum):
    PASS = "pass"
    WARN = "warn"
    REVIEW_REQUIRED = "review_required"
    NOT_CONFIGURED = "not_configured"


@dataclass(frozen=True)
class HighRiskConfirmation:
    """Safe operator record for an explicit high-risk opt-in."""

    confirmed: bool
    source: str = "operator"
    summary: str = ""


class Phase(str, Enum):
    PROJECT_INTAKE = "project_intake"
    EXISTING_TEST_DISCOVERY = "existing_test_discovery"
    TEST_PREPARATION = "test_preparation"
    FREEZE_TEST_SET = "freeze_test_set"
    BASELINE = "baseline"
    READY = "ready"
    DISPATCH = "dispatch"
    EVALUATE = "evaluate"
    FINAL_REVIEW = "final_review"
    FINAL_REVIEW_GATE = "final_review_gate"
    PAUSED = "paused"
    STOP = "stop"


class StopReason(Enum):
    SUCCESS = "success"
    REQUESTED = "requested"
    MAX_STEPS = "max_steps"
    MAX_ROUNDS = "max_rounds"
    NO_PROGRESS = "no_progress"
    ERROR = "error"
    CONFIG_ERROR = "config_error"
    OPERATOR_STOP = "operator_stop"
    TEST_PREPARATION_ERROR = "test_preparation_error"
    FINAL_REVIEW_REJECTED = "final_review_rejected"


def exit_code_for_stop_reason(reason: StopReason) -> int:
    if reason is StopReason.SUCCESS:
        return 0
    if reason in {
        StopReason.REQUESTED,
        StopReason.MAX_STEPS,
        StopReason.MAX_ROUNDS,
        StopReason.NO_PROGRESS,
        StopReason.OPERATOR_STOP,
        StopReason.FINAL_REVIEW_REJECTED,
    }:
        return 1
    if reason is StopReason.CONFIG_ERROR:
        return 2
    return 3


class GuardDecision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class ToolName(str, Enum):
    READ_FILE = "read_file"
    LIST_DIR = "list_dir"
    SEARCH_CODE = "search_code"
    APPLY_PATCH = "apply_patch"
    FINISH = "finish"


@dataclass(frozen=True)
class Change:
    path: str
    old_text: str
    new_text: str


@dataclass(frozen=True)
class ToolCall:
    tool: ToolName
    path: str | None = None
    query: str | None = None
    changes: tuple[Change, ...] = ()
    reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "changes", tuple(self.changes))


@dataclass(frozen=True)
class FailureSet:
    ids: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "ids", frozenset(self.ids))


@dataclass(frozen=True)
class Feedback:
    outcome: str
    summary: str = ""
    labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelRoleConfig:
    role: ModelRole
    base_url: str
    model: str
    keyring_service: str

    @property
    def identity_fingerprint(self) -> str:
        """Return a stable provider/model identity without URL credentials."""
        from .events import sanitize_model_identity

        return sanitize_model_identity(
            f"{self.role.value}:{self.base_url}:{self.model}"
        )


@dataclass
class Config:
    max_steps: int = 30
    max_rounds: int = 10
    max_no_progress_rounds: int = 3
    allowed_paths: list[str] | None = None
    excluded_paths: list[str] = field(default_factory=list)
    pytest_args: list[str] = field(default_factory=list)
    base_url: str = ""
    model: str = ""
    generate_tests: bool = False
    baseline_source: BaselineSource = BaselineSource.EXISTING
    acceptance_mode: AcceptanceMode = AcceptanceMode.STANDARD
    stability_runs: int = 3
    max_auto_accepted_failures: int = 3
    test_base_url: str = ""
    test_model: str = ""
    review_base_url: str = ""
    review_model: str = ""

    def role_config(self, role: ModelRole) -> ModelRoleConfig:
        role = ModelRole(role)
        if role is ModelRole.TEST:
            base_url, model = self.test_base_url, self.test_model
        elif role is ModelRole.REPAIR:
            base_url, model = self.base_url, self.model
        else:
            base_url, model = self.review_base_url, self.review_model
        return ModelRoleConfig(
            role=role,
            base_url=base_url,
            model=model,
            keyring_service=role_service_name(role),
        )


@dataclass(frozen=True)
class SessionResult:
    stop_reason: StopReason
    steps: int = 0
    rounds: int = 0
    no_progress: int = 0
    artifact_path: str | None = None
    exit_code: int = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "exit_code", exit_code_for_stop_reason(self.stop_reason)
        )
