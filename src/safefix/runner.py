from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .approval import ApprovalProvider
from .artifacts import ArtifactWriter
from .config import ConfigError, load_config
from .credentials import CredentialError, CredentialsResolver
from .feedback import FeedbackEngine
from .guardrail import Guardrail
from .llm.base import LLMClient, LLMTransportError
from .models import (
    Config,
    FailureSet,
    Feedback,
    GuardDecision,
    SessionResult,
    StopReason,
    ToolCall,
    ToolName,
)
from .parse import ActionParser, ParseError
from .paths import compute_writable_py_files
from .session_state import SessionState
from .snapshot import SnapshotStore
from .testrunner import TestRunResult, TestRunner
from .tools.dispatch import dispatch


class BaselineRunner(Protocol):
    def run(self) -> TestRunResult:
        """Run the configured pytest baseline."""


TestRunnerFactory = Callable[[Path, list[str]], BaselineRunner]
TRANSPORT_ATTEMPTS = 3


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
            if self.state is None:
                return early_stop
            return self._finalize(early_stop.stop_reason)

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
            stop_reason = self._ready_stop_reason()
            if stop_reason is not None:
                return self._finalize(stop_reason)

            state.increment_step()
            try:
                response = self._complete()
            except LLMTransportError:
                return self._finalize(StopReason.ERROR)
            try:
                action = parser.parse(response)
            except ParseError:
                state.record_tool_event(
                    ToolCall(tool=ToolName.FINISH, reason="parse error"),
                    Feedback("parse_error", "invalid tool call"),
                )
                continue
            decision = guardrail.check(action)
            state.record_guard_event(action, decision)

            if decision is GuardDecision.DENY:
                state.record_tool_event(action, Feedback("denied", "guardrail denied action"))
                continue
            if decision is GuardDecision.REQUIRE_APPROVAL and not self._approval.approve(action):
                state.record_tool_event(action, Feedback("denied", "approval denied action"))
                continue

            if action.tool is ToolName.APPLY_PATCH:
                dispatch(self.project_root, action, self.snapshot_store)
                evaluation = self._test_runner_factory(
                    self.project_root, self.config.pytest_args
                ).run()
                if evaluation.exit_code not in {0, 1}:
                    state.record_tool_event(action, Feedback("error", "test run failed"))
                    return self._finalize(StopReason.ERROR)

                state.increment_round()
                current = FailureSet(evaluation.failure_ids)
                feedback = FeedbackEngine().evaluate(state.U_best, current)
                state.F = current
                state.record_tool_event(action, feedback)

                if feedback.outcome in {"better", "success"}:
                    state.update_best_checkpoint(current)
                    state.reset_no_progress()
                    assert self.snapshot_store is not None
                    self.snapshot_store.best_contents = self.snapshot_store.snapshot_before_apply()
                    if feedback.outcome == "success":
                        return self._finalize(StopReason.SUCCESS)
                    continue

                self._restore_best()
                if feedback.outcome == "same":
                    state.increment_no_progress()
                continue

            outcome = dispatch(self.project_root, action, self.snapshot_store)
            if outcome is StopReason.REQUESTED:
                return self._finalize(StopReason.REQUESTED)
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

    def _ready_stop_reason(self) -> StopReason | None:
        assert self.config is not None
        assert self.state is not None
        if self.state.steps >= self.config.max_steps:
            return StopReason.MAX_STEPS
        if self.state.rounds >= self.config.max_rounds:
            return StopReason.MAX_ROUNDS
        if self.state.no_progress_rounds >= self.config.max_no_progress_rounds:
            return StopReason.NO_PROGRESS
        return None

    def _complete(self) -> str:
        assert self._llm_client is not None
        for attempt in range(TRANSPORT_ATTEMPTS):
            try:
                return self._llm_client.complete(self._prompt())
            except LLMTransportError:
                if attempt == TRANSPORT_ATTEMPTS - 1:
                    raise
        raise AssertionError("transport retry loop exhausted")

    def _finalize(self, stop_reason: StopReason) -> SessionResult:
        if self.snapshot_store is not None:
            self._restore_best()
        return ArtifactWriter(self.project_root / "safefix-session.json").write(
            self.state,
            self._result(stop_reason),
        )

    def _restore_best(self) -> None:
        assert self.state is not None
        assert self.snapshot_store is not None
        self.snapshot_store.restore()
        self.state.F = self.state.U_best
