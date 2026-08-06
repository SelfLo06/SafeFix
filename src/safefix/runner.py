from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
from typing import Protocol

from .approval import ApprovalProvider
from .artifacts import ArtifactWriter
from .config import ConfigError, load_config
from .context import ContextBuilder
from .credentials import CredentialError, CredentialsResolver
from .events import EventSink, SessionEvent
from .feedback import FeedbackEngine
from .guardrail import Guardrail
from .llm.base import LLMClient, LLMResponseError, LLMTransportError
from .models import (
    Config,
    FailureSet,
    Feedback,
    GuardDecision,
    Phase,
    SessionResult,
    StopReason,
    ToolCall,
    ToolName,
)
from .memory import MemoryFormatError, ProjectMemoryStore
from .parse import ActionParser, ParseError
from .operator import OperatorCommandQueue
from .paths import compute_writable_py_files
from .session_state import SessionState
from .snapshot import SnapshotStore
from .session_setup import SessionSetup, manifest_from_entries, runner_for
from .testprep.service import TestPreparationService
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
        event_sink: Callable[[str], None] | EventSink | None = None,
        operator_queue: OperatorCommandQueue | None = None,
        use_memory: bool = False,
        memory_store: ProjectMemoryStore | None = None,
        preparation_factory: object | None = None,
        manifest_factory: object | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self._cli_overrides = {} if cli_overrides is None else cli_overrides
        self._credentials = credentials or CredentialsResolver()
        self._config_loader = config_loader
        self._test_runner_factory = test_runner_factory
        self._llm_client = llm_client
        self._guardrail = guardrail
        self._approval = approval or ApprovalProvider()
        self._event_sink = event_sink
        self._operator_queue = operator_queue
        self._pending_action: ToolCall | None = None
        self._pending_resolution: bool | None = None
        self._pending_event = threading.Event()
        self._event_sequence = 1
        self._use_memory = use_memory
        self._memory_store = memory_store or ProjectMemoryStore(self.project_root)
        self._context_builder = ContextBuilder(self._memory_store)
        self._preparation_factory = preparation_factory
        self._manifest_factory = manifest_factory
        self._setup_selected = (
            self._test_runner_factory is TestRunner
            or self._preparation_factory is not None
            or self._manifest_factory is not None
        )
        self.config: Config | None = None
        self.writable_paths: set[Path] = set()
        self.state: SessionState | None = None
        self.snapshot_store: SnapshotStore | None = None
        self.manifest = None

    def initialize(self) -> SessionResult | None:
        """Run INIT and return an early stop, or prepare later phases."""
        if not self.project_root.is_dir():
            return SessionResult(stop_reason=StopReason.CONFIG_ERROR)
        if self._setup_selected:
            return self._initialize_with_setup()
        return self._initialize_legacy()

    def _initialize_with_setup(self) -> SessionResult | None:
        setup = SessionSetup(
            self.project_root,
            lambda root, _overrides, **kwargs: self._config_loader(
                root, self._cli_overrides, **kwargs
            ),
            self._credentials,
            self._test_runner_factory,
            self._preparation_factory or TestPreparationService,
            self._manifest_factory or manifest_from_entries,
        )
        result = setup.prepare()
        self.config = result.config
        self.writable_paths = set(result.writable_paths)
        self.manifest = result.manifest
        if result.baseline is not None and result.config is not None:
            self.state = SessionState(
                FailureSet(result.baseline.failure_ids),
                acceptance_mode=result.config.acceptance_mode,
                repair_required=bool(result.baseline.failure_ids),
            )
            if result.manifest is not None and result.preparation_summary is not None:
                self.state.set_preparation(result.preparation_summary, result.manifest)
        if result.early_stop is not None:
            return result.early_stop
        if self.state is None or self.config is None or self.manifest is None:
            return SessionResult(stop_reason=StopReason.ERROR)
        if not self.state.F0.ids:
            return SessionResult(stop_reason=StopReason.SUCCESS)
        if not self.writable_paths:
            return SessionResult(stop_reason=StopReason.CONFIG_ERROR)
        self.snapshot_store = SnapshotStore(self.project_root, self.writable_paths)
        return None

    def _initialize_legacy(self) -> SessionResult | None:
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

            if self._pending_action is not None:
                action = self._pending_action
                if self._pending_resolution is False:
                    self._clear_pending_action()
                    state.record_tool_event(
                        action, Feedback("denied", "approval denied action")
                    )
                    continue
                self._clear_pending_action()
                decision = GuardDecision.ALLOW
            else:
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
                    state.record_tool_event(
                        action, Feedback("error", "guardrail check failed")
                    )
                    return self._finalize(StopReason.ERROR)
                state.record_guard_event(action, decision)

            if decision is GuardDecision.DENY:
                self._emit(f"deny tool={action.tool.value}")
                state.record_tool_event(action, Feedback("denied", "guardrail denied action"))
                continue
            if decision is GuardDecision.REQUIRE_APPROVAL:
                self._emit(f"approval tool={action.tool.value} decision=requested")
                if self._operator_queue is not None and self._pending_action is None:
                    self._pending_action = action
                    self._pending_resolution = None
                    self._pending_event.clear()
                    request = getattr(self._approval, "request", None)
                    if callable(request):
                        request(action)
                    self._emit_control(
                        "approval",
                        {"status": "pending", "tool": action.tool.value},
                    )
                    continue
                approved = self._approval.approve(action)
                self._emit(
                    f"approval tool={action.tool.value} decision={'approved' if approved else 'denied'}"
                )
                if not approved:
                    state.record_tool_event(action, Feedback("denied", "approval denied action"))
                    continue

            if action.tool is ToolName.APPLY_PATCH:
                try:
                    dispatch(self.project_root, action, self.snapshot_store)
                except (OSError, ValueError):
                    state.record_tool_event(action, Feedback("error", "patch execution failed"))
                    return self._finalize(StopReason.ERROR)
                try:
                    if self.manifest is not None:
                        self.manifest.verify(self.project_root)
                    evaluation = self._evaluation_runner().run()
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
                self._emit(f"round outcome={feedback.outcome} rounds={state.rounds}")

                if feedback.outcome in {"better", "success"}:
                    assert self.snapshot_store is not None
                    try:
                        state.update_best_checkpoint(current)
                        state.reset_no_progress()
                        self.snapshot_store.update_best()
                    except OSError:
                        state.record_tool_event(
                            action, Feedback("error", "checkpoint update failed")
                        )
                        return self._finalize(StopReason.ERROR)
                    if feedback.outcome == "success":
                        return self._finalize(StopReason.SUCCESS)
                    continue

                state.record_patch_fingerprint(self._patch_fingerprint(action))
                try:
                    self._restore_best()
                except OSError:
                    state.record_tool_event(
                        action, Feedback("error", "checkpoint restore failed")
                    )
                    return self._finalize(StopReason.ERROR)
                state.increment_no_progress()
                continue

            try:
                outcome = dispatch(self.project_root, action, self.snapshot_store)
            except (OSError, ValueError):
                state.record_tool_event(action, Feedback("error", "tool execution failed"))
                continue
            if outcome is StopReason.REQUESTED:
                self._emit(
                    f"finish reason={'[redacted]' if action.reason else ''}"
                )
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

    def _evaluation_runner(self) -> BaselineRunner:
        assert self.config is not None
        if self.manifest is not None:
            return runner_for(
                self._test_runner_factory,
                self.project_root,
                self.config.pytest_args,
                target_paths=tuple(entry.path for entry in self.manifest.entries),
            )
        return self._test_runner_factory(self.project_root, self.config.pytest_args)

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
        stop_reason = self._consume_ready_commands(include_guidance=True)
        if stop_reason is not None:
            return stop_reason
        while self._pending_action is not None and self._pending_resolution is None:
            stop_reason = self._consume_ready_commands(include_guidance=False)
            if stop_reason is not None:
                return stop_reason
            self._pending_event.wait(0.05)
        return None

    def approve_pending(self) -> bool:
        if self._pending_action is None:
            return False
        self._pending_resolution = True
        self._pending_event.set()
        resolve = getattr(self._approval, "approve_pending", None)
        return True if not callable(resolve) else resolve()

    def deny_pending(self) -> bool:
        if self._pending_action is None:
            return False
        self._pending_resolution = False
        self._pending_event.set()
        resolve = getattr(self._approval, "deny_pending", None)
        return True if not callable(resolve) else resolve()

    def _consume_ready_commands(self, *, include_guidance: bool) -> StopReason | None:
        if self._operator_queue is None:
            return None
        commands = self._operator_queue.drain_ready_commands(
            pending_approval=self._pending_action is not None,
            include_ignored_approval=True,
        )
        for command in commands:
            if command.kind == "stop":
                self._emit_control("stop", {"status": "accepted"})
                return StopReason.OPERATOR_STOP
            if command.kind == "approve":
                if self._pending_action is None:
                    self._emit_control("approve", {"status": "ignored"})
                else:
                    self._emit_control("approve", {"status": "accepted"})
                    self.approve_pending()
            elif command.kind == "deny":
                if self._pending_action is None:
                    self._emit_control("deny", {"status": "ignored"})
                else:
                    self._emit_control("deny", {"status": "accepted"})
                    self.deny_pending()
            elif command.kind in {"pause", "resume", "status"}:
                self._emit_control(command.kind, {"status": "observed"})
        if include_guidance:
            for summary in self._operator_queue.drain_ready_guidance():
                self.state.record_guidance(summary)  # type: ignore[union-attr]
                self._emit_control("guidance", {"summary": summary})
        return None

    def _clear_pending_action(self) -> None:
        self._pending_action = None
        self._pending_resolution = None
        self._pending_event.clear()

    def _complete(self) -> str:
        assert self._llm_client is not None
        for attempt in range(TRANSPORT_ATTEMPTS):
            try:
                return self._llm_client.complete(self._prompt())
            except LLMTransportError:
                if attempt == TRANSPORT_ATTEMPTS - 1:
                    raise

    def _finalize(self, stop_reason: StopReason) -> SessionResult:
        final_reason = stop_reason
        if self.snapshot_store is not None:
            try:
                self._restore_best()
            except OSError:
                final_reason = StopReason.ERROR
        assert self.state is not None
        if self._use_memory:
            try:
                self._memory_store.update(
                    f"stop_reason={final_reason.value}; unresolved={len(self.state.U_best.ids)}",
                    unsuccessful_patch_fingerprints=tuple(self.state.patch_fingerprints),
                )
            except (OSError, MemoryFormatError):
                final_reason = StopReason.ERROR
        result = self._result(final_reason)
        try:
            result = ArtifactWriter(self.project_root / "safefix-session.json").write(
                self.state, result
            )
        except OSError:
            result = self._result(StopReason.ERROR)
        self._emit(f"stop reason={result.stop_reason.value} exit_code={result.exit_code}")
        return result

    def _restore_best(self) -> None:
        assert self.state is not None
        assert self.snapshot_store is not None
        self.snapshot_store.restore()
        self.state.F = self.state.U_best

    def _emit(self, event: str, *, payload: dict[str, object] | None = None) -> None:
        if self._event_sink is None:
            return
        emit = getattr(self._event_sink, "emit", None)
        if callable(emit):
            emit(
                SessionEvent(
                    sequence=self._event_sequence,
                    timestamp=datetime.now(timezone.utc).isoformat().replace(
                        "+00:00", "Z"
                    ),
                    phase=Phase.READY,
                    kind="control",
                    safe_payload=payload or {"summary": event},
                )
            )
            self._event_sequence += 1
            return
        if callable(self._event_sink):
            self._event_sink(event)

    def _emit_control(self, command: str, payload: dict[str, object]) -> None:
        self._emit(
            f"control command={command} status={payload.get('status', '')}",
            payload={"command": command, **payload},
        )


def _tool_result_summary(outcome: object) -> str:
    if isinstance(outcome, str):
        rendered = outcome
    elif isinstance(outcome, StopReason):
        rendered = outcome.value
    else:
        rendered = json.dumps(outcome, ensure_ascii=False, sort_keys=True)
    return rendered[:MAX_TOOL_RESULT_CHARS]
