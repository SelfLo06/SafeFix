from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import Protocol

from .approval import ApprovalProvider
from .artifacts import ArtifactWriter
from .config import ConfigError, load_config
from .context import ContextBuilder
from .credentials import CredentialError, CredentialsResolver
from .feedback import FeedbackEngine
from .guardrail import Guardrail
from .llm.base import LLMClient, LLMResponseError, LLMTransportError
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
from .memory import MemoryFormatError, ProjectMemoryStore
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
MAX_TOOL_RESULT_CHARS = 4000


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
        use_memory: bool = False,
        memory_store: ProjectMemoryStore | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self._cli_overrides = {} if cli_overrides is None else cli_overrides
        self._credentials = credentials or CredentialsResolver()
        self._config_loader = config_loader
        self._test_runner_factory = test_runner_factory
        self._llm_client = llm_client
        self._guardrail = guardrail
        self._approval = approval or ApprovalProvider()
        self._use_memory = use_memory
        self._memory_store = memory_store or ProjectMemoryStore(self.project_root)
        self._context_builder = ContextBuilder(self._memory_store)
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

        try:
            self.writable_paths = compute_writable_py_files(
                self.project_root, self.config.allowed_paths, self.config.excluded_paths
            )
        except ValueError:
            return SessionResult(stop_reason=StopReason.CONFIG_ERROR)
        try:
            baseline = self._test_runner_factory(
                self.project_root, self.config.pytest_args
            ).run()
        except (OSError, ValueError):
            return SessionResult(stop_reason=StopReason.ERROR)
        if not baseline.valid:
            stop_reason = (
                StopReason.ERROR if baseline.exit_code == 3 else StopReason.CONFIG_ERROR
            )
            return SessionResult(stop_reason=stop_reason)
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
            return self._finalize(StopReason.ERROR)

        state = self.state
        assert state is not None
        try:
            guardrail = self._guardrail or Guardrail(
                self.project_root,
                {path.relative_to(self.project_root) for path in self.writable_paths},
            )
        except (OSError, ValueError):
            return self._finalize(StopReason.ERROR)
        parser = ActionParser(self.project_root)

        while True:
            stop_reason = self._ready_stop_reason()
            if stop_reason is not None:
                return self._finalize(stop_reason)

            state.increment_step()
            try:
                response = self._complete()
            except (LLMResponseError, LLMTransportError, MemoryFormatError):
                return self._finalize(StopReason.ERROR)
            try:
                action = parser.parse(response)
            except ParseError:
                state.record_tool_event(
                    ToolCall(tool=ToolName.FINISH, reason="parse error"),
                    Feedback("parse_error", "invalid tool call"),
                )
                continue
            try:
                decision = guardrail.check(action)
            except (OSError, ValueError):
                state.record_tool_event(action, Feedback("error", "guardrail check failed"))
                return self._finalize(StopReason.ERROR)
            state.record_guard_event(action, decision)

            if decision is GuardDecision.DENY:
                state.record_tool_event(action, Feedback("denied", "guardrail denied action"))
                continue
            if decision is GuardDecision.REQUIRE_APPROVAL and not self._approval.approve(action):
                state.record_tool_event(action, Feedback("denied", "approval denied action"))
                continue

            if action.tool is ToolName.APPLY_PATCH:
                try:
                    dispatch(self.project_root, action, self.snapshot_store)
                except (OSError, ValueError):
                    state.record_tool_event(action, Feedback("error", "patch execution failed"))
                    return self._finalize(StopReason.ERROR)
                try:
                    evaluation = self._test_runner_factory(
                        self.project_root, self.config.pytest_args
                    ).run()
                except (OSError, ValueError):
                    state.record_tool_event(action, Feedback("error", "test run failed"))
                    return self._finalize(StopReason.ERROR)
                if not evaluation.valid:
                    state.record_tool_event(action, Feedback("error", "test run failed"))
                    return self._finalize(StopReason.ERROR)

                state.increment_round()
                current = FailureSet(evaluation.failure_ids)
                feedback = FeedbackEngine().evaluate(state.F0, state.U_best, current)
                status_labels = {
                    "failed": str(sum(case.status == "failed" for case in evaluation.cases)),
                    "error": str(sum(case.status == "error" for case in evaluation.cases)),
                }
                feedback = replace(
                    feedback,
                    labels={**feedback.labels, **status_labels},
                )
                state.F = current
                state.last_evaluated = current
                state.last_feedback = feedback
                state.record_tool_event(action, feedback)

                if feedback.outcome in {"better", "success"}:
                    state.update_best_checkpoint(current)
                    state.reset_no_progress()
                    assert self.snapshot_store is not None
                    self.snapshot_store.update_best()
                    if feedback.outcome == "success":
                        return self._finalize(StopReason.SUCCESS)
                    continue

                state.record_patch_fingerprint(self._patch_fingerprint(action))
                self._restore_best()
                state.increment_no_progress()
                continue

            try:
                outcome = dispatch(self.project_root, action, self.snapshot_store)
            except (OSError, ValueError):
                state.record_tool_event(action, Feedback("error", "tool execution failed"))
                continue
            if outcome is StopReason.REQUESTED:
                return self._finalize(StopReason.REQUESTED)
            state.record_tool_event(
                action,
                Feedback("completed", summary=_tool_result_summary(outcome)),
            )

    def _prompt(self) -> str:
        assert self.state is not None
        context = self._context_builder.build(
            self.state, use_memory=self._use_memory
        )
        return (
            "Return exactly one SafeFix ToolCall JSON object.\n"
            + json.dumps(context, sort_keys=True)
        )

    @staticmethod
    def _patch_fingerprint(action: ToolCall) -> str:
        payload = [
            {"path": change.path, "old_text": change.old_text, "new_text": change.new_text}
            for change in action.changes
        ]
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

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

    def _finalize(self, stop_reason: StopReason) -> SessionResult:
        try:
            if self.snapshot_store is not None:
                self._restore_best()
            result = ArtifactWriter(self.project_root / "safefix-session.json").write(
                self.state,
                self._result(stop_reason),
            )
        except (OSError, MemoryFormatError):
            return self._result(StopReason.ERROR)
        assert self.state is not None
        if self._use_memory:
            try:
                self._memory_store.update(
                    f"stop_reason={stop_reason.value}; unresolved={len(self.state.U_best.ids)}",
                    unsuccessful_patch_fingerprints=tuple(self.state.patch_fingerprints),
                )
            except (OSError, MemoryFormatError):
                error_result = self._result(StopReason.ERROR)
                try:
                    result = ArtifactWriter(
                        self.project_root / "safefix-session.json"
                    ).write(self.state, error_result)
                except OSError:
                    return error_result
        return result

    def _restore_best(self) -> None:
        assert self.state is not None
        assert self.snapshot_store is not None
        self.snapshot_store.restore()
        self.state.F = self.state.U_best


def _tool_result_summary(outcome: object) -> str:
    if isinstance(outcome, str):
        rendered = outcome
    else:
        rendered = json.dumps(outcome, ensure_ascii=False, sort_keys=True, default=str)
    return rendered[:MAX_TOOL_RESULT_CHARS]
