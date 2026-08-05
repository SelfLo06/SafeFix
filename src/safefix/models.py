from dataclasses import dataclass, field
from enum import Enum
class StopReason(Enum):
    SUCCESS = "success"
    REQUESTED = "requested"
    MAX_STEPS = "max_steps"
    MAX_ROUNDS = "max_rounds"
    NO_PROGRESS = "no_progress"
    ERROR = "error"
    CONFIG_ERROR = "config_error"


def exit_code_for_stop_reason(reason: StopReason) -> int:
    if reason is StopReason.SUCCESS:
        return 0
    if reason in {
        StopReason.REQUESTED,
        StopReason.MAX_STEPS,
        StopReason.MAX_ROUNDS,
        StopReason.NO_PROGRESS,
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
