from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .approval import ApprovalProvider
from .config import ConfigError, load_config
from .credentials import CredentialError, CredentialsResolver
from .guardrail import Guardrail
from .llm.base import LLMClient
from .models import (
    Config,
    FailureSet,
    Feedback,
    GuardDecision,
    SessionResult,
    StopReason,
    ToolName,
)
from .parse import ActionParser
from .paths import compute_writable_py_files
from .session_state import SessionState
from .snapshot import SnapshotStore
from .testrunner import TestRunResult, TestRunner
from .tools.dispatch import dispatch


class BaselineRunner(Protocol):
    def run(self) -> TestRunResult:
        """Run the configured pytest baseline."""


TestRunnerFactory = Callable[[Path, list[str]], BaselineRunner]


class SessionRunner:
    """Initialize one repair session through its validated baseline."""

    def __init__(
        self,
        project_root: Path,
        *,
        cli_overrides: dict | None = None,
        credentials: CredentialsResolver | None = None,
        config_loader: Callable[..., Config] = load_config,
        test_runner_factory: TestRunnerFactory = TestRunner,
        llm_client: LLMClient | None = None,
        guardrail: Guardrail | None = None,
        approval: ApprovalProvider | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self._cli_overrides = {} if cli_overrides is None else cli_overrides
        self._credentials = credentials or CredentialsResolver()
        self._config_loader = config_loader
        self._test_runner_factory = test_runner_factory
        self._llm_client = llm_client
        self._guardrail = guardrail
        self._approval = approval or ApprovalProvider()
        self.config: Config | None = None
        self.writable_paths: set[Path] = set()
        self.state: SessionState | None = None
        self.snapshot_store: SnapshotStore | None = None

    def initialize(self) -> SessionResult | None:
        """Run INIT and return an early stop, or prepare later phases."""
        try:
            self.config = self._config_loader(
                self.project_root, self._cli_overrides, require_llm=True
            )
            self._credentials.get()
        except (ConfigError, CredentialError):
            return SessionResult(stop_reason=StopReason.CONFIG_ERROR)

        self.writable_paths = compute_writable_py_files(
            self.project_root, self.config.allowed_paths, self.config.excluded_paths
        )
        baseline = self._test_runner_factory(
            self.project_root, self.config.pytest_args
        ).run()
        if baseline.exit_code not in {0, 1}:
            return SessionResult(stop_reason=StopReason.CONFIG_ERROR)

        self.state = SessionState(FailureSet(baseline.failure_ids))
        if not self.state.F0.ids:
            return SessionResult(stop_reason=StopReason.SUCCESS)
        if not self.writable_paths:
            return SessionResult(stop_reason=StopReason.CONFIG_ERROR)

        self.snapshot_store = SnapshotStore(self.project_root, self.writable_paths)
        return None

    def run(self) -> SessionResult:
        """Run READY and DISPATCH until an early or requested stop."""
        early_stop = self.initialize()
        if early_stop is not None:
            return early_stop

        if self._llm_client is None:
            raise RuntimeError("READY requires an LLM client")

        state = self.state
        assert state is not None
        guardrail = self._guardrail or Guardrail(
            self.project_root,
            {path.relative_to(self.project_root) for path in self.writable_paths},
        )
        parser = ActionParser(self.project_root)

        while True:
            state.increment_step()
            action = parser.parse(self._llm_client.complete(self._prompt()))
            decision = guardrail.check(action)
            state.record_guard_event(action, decision)

            if decision is GuardDecision.DENY:
                state.record_tool_event(action, Feedback("denied", "guardrail denied action"))
                continue
            if decision is GuardDecision.REQUIRE_APPROVAL and not self._approval.approve(action):
                state.record_tool_event(action, Feedback("denied", "approval denied action"))
                continue

            if action.tool is ToolName.APPLY_PATCH:
                raise NotImplementedError("EVALUATE is implemented in Task 13c")

            outcome = dispatch(self.project_root, action, self.snapshot_store)
            if outcome is StopReason.REQUESTED:
                return self._result(StopReason.REQUESTED)
            state.record_tool_event(action, Feedback("completed"))

    @staticmethod
    def _prompt() -> str:
        return "Return exactly one SafeFix ToolCall JSON object."

    def _result(self, stop_reason: StopReason) -> SessionResult:
        assert self.state is not None
        return SessionResult(
            stop_reason=stop_reason,
            steps=self.state.steps,
            rounds=self.state.rounds,
            no_progress=self.state.no_progress_rounds,
        )
