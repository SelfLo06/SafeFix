from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from difflib import unified_diff
from pathlib import Path
import threading
import time
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
    AcceptanceMode,
    BaselineSource,
    Config,
    FailureSet,
    Feedback,
    GuardDecision,
    ModelRole,
    Phase,
    ReviewVerdict,
    SessionResult,
    StopReason,
    ToolCall,
    ToolName,
)
from .memory import MemoryFormatError, ProjectMemoryStore
from .parse import ActionParser, ParseError
from .operator import OperatorCommandQueue
from .paths import compute_writable_py_files
from .session_state import SessionState, safe_summary
from .snapshot import SnapshotStore
from .review import FinalReviewRequest, FinalReviewService, ReviewClient, ReviewParseError, ReviewResult
from .session_setup import SessionSetup, manifest_from_entries, runner_for
from .testprep.service import TestPreparationService
from .testrunner import TestRunResult, TestRunner


MAX_FINAL_REVIEW_DIFF_CHARS = 24_000
MAX_FINAL_REVIEW_FILE_DIFF_CHARS = 6_000
from .tools.dispatch import dispatch


class BaselineRunner(Protocol):
    def run(self) -> TestRunResult:
        """Run the configured pytest baseline."""


TestRunnerFactory = Callable[[Path, list[str]], BaselineRunner]
TRANSPORT_ATTEMPTS = 3
MAX_TOOL_RESULT_CHARS = 4000
MAX_STATUS_FAILURES = 20


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
        test_client: object | None = None,
        review_client: ReviewClient | None = None,
        test_client_factory: Callable[[], object] | None = None,
        review_client_factory: Callable[[], ReviewClient] | None = None,
        guardrail: Guardrail | None = None,
        approval: ApprovalProvider | None = None,
        event_sink: Callable[[str], None] | EventSink | None = None,
        operator_queue: OperatorCommandQueue | None = None,
        use_memory: bool = False,
        memory_store: ProjectMemoryStore | None = None,
        preparation_factory: object | None = None,
        manifest_factory: object | None = None,
        final_review_client: ReviewClient | None = None,
        final_review_service: FinalReviewService | None = None,
        high_risk_confirmation: bool | None = None,
        progress_events: bool = False,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self._cli_overrides = {} if cli_overrides is None else cli_overrides
        self._credentials = credentials or CredentialsResolver()
        self._config_loader = config_loader
        self._test_runner_factory = test_runner_factory
        self._llm_client = llm_client
        self._test_client = test_client
        self._review_client = review_client
        self._test_client_factory = test_client_factory
        self._review_client_factory = review_client_factory
        self._guardrail = guardrail
        self._approval = approval or ApprovalProvider()
        self._event_sink = event_sink
        self._operator_queue = operator_queue
        self._pending_action: ToolCall | None = None
        self._pending_final_review: FinalReviewRequest | None = None
        self._pending_resolution: bool | None = None
        self._pending_event = threading.Event()
        self._phase = Phase.READY
        self._last_result: SessionResult | None = None
        self._event_sequence = 1
        self._use_memory = use_memory
        self._memory_store = memory_store or ProjectMemoryStore(self.project_root)
        self._context_builder = ContextBuilder(self._memory_store)
        self._preparation_factory = preparation_factory
        self._manifest_factory = manifest_factory
        self._final_review_client = final_review_client
        self._final_review_service = final_review_service or FinalReviewService()
        self._high_risk_confirmation = high_risk_confirmation
        self._progress_events = progress_events
        self._recent_tool_results: list[str] = []
        self._prepared = False
        self._prepare_result: SessionResult | None = None
        self._preflight_failure_detail: str | None = None
        self._last_success_evaluation: TestRunResult | None = None
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

    @property
    def phase(self) -> Phase:
        return self._phase

    @property
    def pending_approval(self) -> bool:
        return (
            (self._pending_action is not None or self._pending_final_review is not None)
            and self._pending_resolution is None
        ) or bool(getattr(self._approval, "pending", False))

    def initialize(self) -> SessionResult | None:
        """Run INIT and return an early stop, or prepare later phases."""
        if not self.project_root.is_dir():
            return SessionResult(stop_reason=StopReason.CONFIG_ERROR)
        if self._setup_selected:
            return self._initialize_with_setup()
        return self._initialize_legacy()

    def prepare(self) -> SessionResult | None:
        """Freeze setup and baseline once before an interactive repair start."""
        if not self._prepared:
            self._prepare_result = self.initialize()
            self._prepared = True
        return self._prepare_result

    @property
    def preflight_failure_detail(self) -> str | None:
        return self._preflight_failure_detail

    def configure_preflight(self, *, tests: str | None = None, review: bool | None = None) -> None:
        if tests is not None and self._prepared:
            raise RuntimeError("baseline is already frozen")
        if tests is not None:
            source = BaselineSource(tests)
            self._cli_overrides["baseline_source"] = source.value
            self._cli_overrides["generate_tests"] = source is not BaselineSource.EXISTING
            if source is not BaselineSource.EXISTING and self._test_client is None:
                if self._test_client_factory is None:
                    raise RuntimeError("Test Model is not configured")
                self._test_client = self._test_client_factory()
        if review is not None:
            if review and self._final_review_client is None:
                if self._review_client_factory is None:
                    raise RuntimeError("Review Model is not configured")
                self._review_client = self._review_client_factory()
                self._final_review_client = self._review_client
            if not review:
                self._review_client = None
                self._final_review_client = None

    def run_final_review_now(self) -> ReviewResult:
        if self.state is None or self._last_result is None or self._last_success_evaluation is None:
            raise RuntimeError("a successful repair is required before manual review")
        if self.state.review_result is not None:
            raise RuntimeError("Final Review is already recorded.")
        if self._final_review_client is None:
            if self._review_client_factory is None:
                raise RuntimeError("Review Model is not configured")
            self._review_client = self._review_client_factory()
            self._final_review_client = self._review_client
        review = self._final_review_service.review(
            self._final_review_request(self._last_success_evaluation), self._final_review_client
        )
        self.state.set_review(review)
        self._last_result = ArtifactWriter(
            self.project_root / "safefix-session.json"
        ).write(self.state, self._last_result)
        return review

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
            test_client=self._test_client,
            review_client=self._review_client,
            approval_provider=self._approval,
            event_sink=self._event_sink,
            high_risk_confirmation=self._high_risk_confirmation,
        )
        result = setup.prepare()
        self._preflight_failure_detail = result.failure_detail
        self.config = result.config
        if self.config is not None and self._high_risk_review_unavailable(self.config):
            return SessionResult(stop_reason=StopReason.CONFIG_ERROR)
        self.writable_paths = set(result.writable_paths)
        self.manifest = result.manifest
        if result.baseline is not None and result.config is not None:
            self.state = SessionState(
                FailureSet(result.baseline.failure_ids),
                test_model_identity=(
                    result.config.role_config(ModelRole.TEST)
                    if result.config.test_base_url.strip() and result.config.test_model.strip()
                    else None
                ),
                repair_model_identity=result.config.role_config(ModelRole.REPAIR),
                review_model_identity=(
                    result.config.role_config(ModelRole.REVIEW)
                    if result.config.review_base_url.strip() and result.config.review_model.strip()
                    else None
                ),
                acceptance_mode=result.config.acceptance_mode,
                repair_required=bool(result.baseline.failure_ids),
            )
            if self._high_risk_confirmation is True:
                self.state.set_high_risk_confirmation(
                    {
                        "confirmed": True,
                        "summary": "explicit high-risk acceptance confirmed",
                    }
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
        if self._high_risk_review_unavailable(self.config):
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
        self._emit_progress(
            "pytest",
            "Preparing and running the baseline test set.",
            status="running",
            phase=Phase.BASELINE,
        )
        early_stop = self.prepare()
        if early_stop is not None:
            if self.state is None:
                self._emit_progress(
                    "terminal",
                    self._startup_failure_summary(early_stop.stop_reason),
                    status="error",
                    phase=Phase.STOP,
                )
                return early_stop
            return self._finalize(early_stop.stop_reason)

        if self._llm_client is None:
            return self._finalize(StopReason.ERROR)

        state = self.state
        assert state is not None
        self._emit_progress(
            "pytest",
            f"Baseline ready: {len(state.F0.ids)} failing test(s) frozen for repair.",
            status="completed",
            phase=Phase.READY,
        )
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
                except ParseError as error:
                    state.record_tool_event(
                        ToolCall(tool=ToolName.FINISH, reason="parse error"),
                        Feedback("parse_error", str(error)),
                    )
                    self._emit_progress(
                        "model-call",
                        f"Repair Model response rejected: {error}.",
                        status="error",
                        phase=Phase.DISPATCH,
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
                        if feedback.outcome == "success" and self._is_high_risk_final_review():
                            self.snapshot_store.capture_pre_final_best()
                            state.capture_pre_final_best()
                        state.update_best_checkpoint(current)
                        state.reset_no_progress()
                        self.snapshot_store.update_best()
                    except OSError:
                        state.record_tool_event(
                            action, Feedback("error", "checkpoint update failed")
                        )
                        return self._finalize(StopReason.ERROR)
                    if feedback.outcome == "success":
                        self._last_success_evaluation = evaluation
                        stop_reason = self._ready_stop_reason()
                        if stop_reason is not None:
                            return self._finalize(stop_reason)
                        return self._complete_final_review(evaluation)
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
                self._emit_progress(
                    "tool",
                    f"Running {action.tool.value}"
                    + (f" · {action.path}" if action.path else ""),
                    status="running",
                    phase=Phase.DISPATCH,
                )
                outcome = dispatch(self.project_root, action, self.snapshot_store)
            except (OSError, ValueError):
                state.record_tool_event(action, Feedback("error", "tool execution failed"))
                self._emit_progress(
                    "tool",
                    f"{action.tool.value} failed",
                    status="error",
                    phase=Phase.DISPATCH,
                )
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
            if outcome is not None:
                rendered = _tool_result_summary(outcome)
                self._recent_tool_results.append(rendered)
                self._recent_tool_results = self._recent_tool_results[-5:]
            self._emit_progress(
                "tool",
                f"{action.tool.value} completed",
                status="completed",
                phase=Phase.DISPATCH,
            )

    def _prompt(self) -> str:
        assert self.state is not None
        context = self._context_builder.build(
            self.state, use_memory=self._use_memory
        )
        contract = (
            "Return exactly one JSON object for one SafeFix tool action.\n"
            "Do not use Markdown fences, explanations, or any text outside the JSON object.\n"
            'The top-level "tool" must be a string and must be one of: '
            "read_file, list_dir, search_code, apply_patch, finish.\n"
            "Do not return arrays, tool_calls, or multiple actions.\n"
            "Use exactly these fields for the selected tool; do not add unknown fields:\n"
            'read_file: {"tool":"read_file","path":"project-relative/path"}\n'
            'list_dir: {"tool":"list_dir","path":"project-relative/path"}\n'
            'search_code: {"tool":"search_code","path":"project-relative/path","query":"text"}\n'
            'apply_patch: {"tool":"apply_patch","changes":[{"path":"project-relative/path","old_text":"...","new_text":"..."}]}\n'
            'finish: {"tool":"finish","reason":"short reason"} (reason is optional).\n'
            "Paths must be project-relative. Never include API keys, hidden reasoning, or secrets.\n"
        )
        if self._recent_tool_results:
            context["recent_tool_results"] = list(self._recent_tool_results)
        return contract + json.dumps(context, sort_keys=True)

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

        if self._phase is Phase.PAUSED:
            return self._wait_while_paused()

        if self.state.steps >= self.config.max_steps:
            return StopReason.MAX_STEPS
        if self.state.rounds >= self.config.max_rounds:
            return StopReason.MAX_ROUNDS
        if self.state.no_progress_rounds >= self.config.max_no_progress_rounds:
            return StopReason.NO_PROGRESS
        stop_reason = self._consume_ready_commands(include_guidance=False)
        if stop_reason is not None:
            return stop_reason
        if self._phase is Phase.PAUSED:
            return self._wait_while_paused()
        stop_reason = self._answer_ready_explanations()
        if stop_reason is not None:
            return stop_reason
        self._consume_ready_guidance()
        while self.pending_approval:
            stop_reason = self._consume_ready_commands(include_guidance=False)
            if stop_reason is not None:
                return stop_reason
            if self._phase is Phase.PAUSED:
                return self._wait_while_paused()
            stop_reason = self._answer_ready_explanations()
            if stop_reason is not None:
                return stop_reason
            self._pending_event.wait(0.05)
        return None

    def _wait_while_paused(self) -> StopReason | None:
        self._pending_event.clear()
        while self._phase is Phase.PAUSED:
            stop_reason = self._consume_ready_commands(include_guidance=False)
            if stop_reason is not None:
                return stop_reason
            if self._phase is not Phase.PAUSED:
                break
            stop_reason = self._answer_ready_explanations()
            if stop_reason is not None:
                return stop_reason
            self._pending_event.wait(0.05)
            self._pending_event.clear()
        return None

    def approve_pending(self) -> bool:
        if not self.pending_approval:
            return False
        self._pending_resolution = True
        self._pending_event.set()
        resolve = getattr(self._approval, "approve_pending", None)
        return True if not callable(resolve) else resolve()

    def deny_pending(self) -> bool:
        if not self.pending_approval:
            return False
        self._pending_resolution = False
        self._pending_event.set()
        resolve = getattr(self._approval, "deny_pending", None)
        return True if not callable(resolve) else resolve()

    def answer_explanation(self, question: str) -> str:
        """Answer a post-run read-only question without restarting repair."""
        if self.state is None:
            raise RuntimeError("session has not prepared a baseline")
        response = self._complete_explanation(question)
        self.state.record_explanation(question, response)
        self._emit_event(
            "explain", response, status="completed", phase=self._phase, raw_text=response
        )
        if self._last_result is not None:
            self._last_result = ArtifactWriter(
                self.project_root / "safefix-session.json"
            ).write(self.state, self._last_result)
        return response

    def _consume_ready_commands(self, *, include_guidance: bool) -> StopReason | None:
        if self._operator_queue is None:
            return None
        commands = self._operator_queue.drain_ready_commands(
            pending_approval=self.pending_approval,
            include_ignored_approval=True,
        )
        for command in commands:
            if command.kind == "stop":
                self._emit_control("stop", {"status": "accepted"})
                return StopReason.OPERATOR_STOP
            if command.kind == "approve":
                if not self.pending_approval or (
                    self._pending_final_review is not None and self._phase is Phase.PAUSED
                ):
                    self._emit_control("approve", {"status": "ignored"})
                else:
                    self._emit_control("approve", {"status": "accepted"})
                    self.approve_pending()
            elif command.kind == "deny":
                if not self.pending_approval or (
                    self._pending_final_review is not None and self._phase is Phase.PAUSED
                ):
                    self._emit_control("deny", {"status": "ignored"})
                else:
                    self._emit_control("deny", {"status": "accepted"})
                    self.deny_pending()
            elif command.kind == "pause":
                if self._phase is Phase.PAUSED:
                    self._emit_control("pause", {"status": "ignored"})
                else:
                    self._phase = Phase.PAUSED
                    self._pending_event.set()
                    self._emit_control("pause", {"status": "accepted"})
            elif command.kind == "resume":
                if self._phase is not Phase.PAUSED:
                    self._emit_control("resume", {"status": "ignored"})
                else:
                    self._phase = Phase.READY
                    self._pending_event.set()
                    self._emit_control("resume", {"status": "accepted"})
            elif command.kind == "status":
                self._emit_control("status", self._status_payload())
        if include_guidance:
            self._consume_ready_guidance()
        return None

    def _consume_ready_guidance(self) -> None:
        if self._operator_queue is None:
            return
        guidance = self._operator_queue.drain_ready_guidance()
        if not guidance:
            return
        for summary in guidance:
            self.state.record_guidance(summary)  # type: ignore[union-attr]
        self._emit_event(
            "guidance",
            f"Applying {len(guidance)} operator guidance message(s) to the next decision.",
            phase=Phase.READY,
        )

    def _answer_ready_explanations(self) -> StopReason | None:
        if self._operator_queue is None:
            return None
        questions = self._operator_queue.drain_ready_explanations()
        for question in questions:
            try:
                response = self._complete_explanation(question)
            except (LLMResponseError, LLMTransportError):
                return StopReason.ERROR
            assert self.state is not None
            self.state.record_explanation(question, response)
            self._emit_event(
                "explain",
                response,
                status="completed",
                phase=self._phase,
                raw_text=response,
            )
        return None

    def _complete_explanation(self, question: str) -> str:
        assert self._llm_client is not None
        assert self.state is not None
        prompt = (
            "Reply in Chinese. Provide a concise read-only explanation for the SafeFix operator in plain terminal text. "
            "Usually keep it within about 12 short lines; expand modestly when the question needs context. "
            "Do not start with 'SafeFix', do not use Markdown headings, bold, code fences, or tables. "
            "Do not propose or encode a ToolCall, do not modify files, and do not reveal "
            "private reasoning or secrets.\n"
            f"Question: {question}\n"
            "Safe session context:\n"
            + json.dumps(self._context_builder.build(self.state, use_memory=self._use_memory), sort_keys=True)
        )
        for attempt in range(TRANSPORT_ATTEMPTS):
            attempt_number = attempt + 1
            self._emit_event(
                "explain",
                f"Explain request {attempt_number} of {TRANSPORT_ATTEMPTS} in progress.",
                status="running",
                phase=self._phase,
            )
            try:
                return self._llm_client.complete(prompt)
            except LLMTransportError as error:
                if attempt == TRANSPORT_ATTEMPTS - 1:
                    self._emit_event(
                        "explain",
                        self._transport_failure_summary(error),
                        status="error",
                        phase=self._phase,
                    )
                    raise
                self._emit_event(
                    "explain",
                    f"{self._transport_failure_summary(error)} Retrying next request.",
                    status="retrying",
                    phase=self._phase,
                )
            except LLMResponseError:
                self._emit_event(
                    "explain",
                    "Explain request returned an invalid response.",
                    status="error",
                    phase=self._phase,
                )
                raise
        raise AssertionError("bounded explain retry loop exhausted without a result")

    def _is_high_risk_final_review(self) -> bool:
        assert self.config is not None
        return self.config.acceptance_mode is AcceptanceMode.HIGH_RISK

    def _high_risk_review_unavailable(self, config: Config) -> bool:
        return config.acceptance_mode is AcceptanceMode.HIGH_RISK and (
            not config.review_base_url.strip()
            or not config.review_model.strip()
            or self._final_review_client is None
        )

    def _complete_final_review(self, evaluation: TestRunResult) -> SessionResult:
        assert self.state is not None
        if self.manifest is None:
            return self._finalize(StopReason.SUCCESS)
        if self._final_review_client is None:
            return self._finalize(
                StopReason.CONFIG_ERROR
                if self._is_high_risk_final_review()
                else StopReason.SUCCESS
            )

        self._phase = Phase.FINAL_REVIEW
        request = self._final_review_request(evaluation)
        self._emit_event(
            "review",
            "Review Model request in progress.",
            status="running",
            phase=Phase.FINAL_REVIEW,
        )
        try:
            review = self._final_review_service.review(request, self._final_review_client)
        except (ReviewParseError, LLMResponseError, LLMTransportError, OSError, ValueError):
            self._emit_event(
                "review",
                "Final Review failed.",
                status="error",
                phase=Phase.FINAL_REVIEW,
            )
            return self._finalize(StopReason.ERROR)
        self.state.set_review(review)
        self._emit_event(
            "review",
            f"Final Review {review.verdict.value}: {review.summary}",
            status="completed",
            phase=Phase.FINAL_REVIEW,
        )
        if (
            self._is_high_risk_final_review()
            and review.verdict is ReviewVerdict.REVIEW_REQUIRED
        ):
            self._phase = Phase.FINAL_REVIEW_GATE
            if self._operator_queue is not None:
                self._pending_final_review = request
                self._pending_resolution = None
                self._pending_event.clear()
                request_approval = getattr(self._approval, "request", None)
                if callable(request_approval):
                    request_approval(request)
                self._emit_control(
                    "approval",
                    {"status": "pending", "tool": Phase.FINAL_REVIEW_GATE.value},
                )
                while self.pending_approval:
                    stop_reason = self._consume_ready_commands(include_guidance=False)
                    if stop_reason is not None:
                        return self._finalize(stop_reason)
                    if self._phase is Phase.PAUSED:
                        stop_reason = self._wait_while_paused()
                        if stop_reason is not None:
                            return self._finalize(stop_reason)
                        self._pending_event.clear()
                        continue
                    self._pending_event.wait(0.05)
                approved = self._pending_resolution is True
                self._clear_final_review_approval()
            else:
                approved = self._approval.approve(request)
            if not approved:
                try:
                    assert self.snapshot_store is not None
                    self.snapshot_store.restore_pre_final_best()
                    self.state.restore_pre_final_best()
                except OSError:
                    return self._finalize(StopReason.ERROR)
                return self._finalize(StopReason.FINAL_REVIEW_REJECTED)
        return self._finalize(StopReason.SUCCESS)

    def _final_review_request(self, evaluation: TestRunResult) -> FinalReviewRequest:
        assert self.state is not None
        assert self.snapshot_store is not None
        changed_files = tuple(sorted(self.snapshot_store.best_contents))
        patch_diffs = self._final_review_patch_diffs(changed_files)
        return FinalReviewRequest(
            baseline_summary=(
                f"frozen baseline failures={len(self.state.F0.ids)}; "
                f"failure_ids={','.join(sorted(self.state.F0.ids))}"
            ),
            final_diff_summary=f"changed_files={len(changed_files)}",
            changed_files=changed_files,
            patch_diffs=patch_diffs,
            constraints=(
                "Harness owns F0, frozen-manifest pytest results, and patch state; "
                "the Review Model may only provide a verdict."
            ),
            pytest_summary=(
                f"valid={evaluation.valid}; failures={len(evaluation.failure_ids)}; "
                f"cases={len(evaluation.cases)}"
            ),
        )

    def _final_review_patch_diffs(
        self, changed_files: tuple[str, ...]
    ) -> tuple[tuple[str, str], ...]:
        assert self.snapshot_store is not None
        remaining = MAX_FINAL_REVIEW_DIFF_CHARS
        rendered: list[tuple[str, str]] = []
        for path in changed_files:
            if remaining <= 0:
                break
            before = self.snapshot_store.baseline_contents[path]
            after = self.snapshot_store.best_contents[path]
            diff = "".join(
                unified_diff(
                    before.splitlines(keepends=True),
                    after.splitlines(keepends=True),
                    fromfile=path,
                    tofile=path,
                    n=3,
                )
            )
            excerpt = diff[: min(MAX_FINAL_REVIEW_FILE_DIFF_CHARS, remaining)]
            remaining -= len(excerpt)
            rendered.append((path, excerpt))
        return tuple(rendered)

    def _status_payload(self) -> dict[str, object]:
        assert self.state is not None
        current_failures = sorted(self.state.F.ids)
        best_failures = sorted(self.state.U_best.ids)
        pending_tool = None
        if self.pending_approval:
            if self._pending_action is not None:
                pending_tool = self._pending_action.tool.value
            else:
                pending_tool = Phase.FINAL_REVIEW_GATE.value
        return {
            "status": "snapshot",
            "phase": self._phase.value,
            "step": self.state.steps,
            "round": self.state.rounds,
            "no_progress": self.state.no_progress_rounds,
            "unresolved_failures": [
                safe_summary(failure_id)
                for failure_id in current_failures[:MAX_STATUS_FAILURES]
            ],
            "best_checkpoint": {
                "unresolved_count": len(self.state.U_best.ids),
                "failure_ids": [
                    safe_summary(failure_id)
                    for failure_id in best_failures[:MAX_STATUS_FAILURES]
                ],
            },
            "pending_approval": {
                "pending": self.pending_approval,
                "tool": pending_tool,
            },
        }

    def _clear_pending_action(self) -> None:
        self._pending_action = None
        self._pending_resolution = None
        self._pending_event.clear()

    def _clear_final_review_approval(self) -> None:
        self._pending_final_review = None
        self._pending_resolution = None
        self._pending_event.clear()

    def _complete(self) -> str:
        assert self._llm_client is not None
        for attempt in range(TRANSPORT_ATTEMPTS):
            attempt_number = attempt + 1
            started_at = time.monotonic()
            self._emit_progress(
                "model-call",
                f"Repair Model request {attempt_number} of {TRANSPORT_ATTEMPTS} in progress.",
                status="running",
                phase=Phase.DISPATCH,
            )
            try:
                response = self._llm_client.complete(self._prompt())
            except LLMTransportError as error:
                if attempt == TRANSPORT_ATTEMPTS - 1:
                    self._emit_progress(
                        "model-call",
                        self._transport_failure_summary(error),
                        status="error",
                        phase=Phase.DISPATCH,
                    )
                    raise
                self._emit_progress(
                    "model-call",
                    f"{self._transport_failure_summary(error)} Retrying next request.",
                    status="retrying",
                    phase=Phase.DISPATCH,
                )
            except LLMResponseError:
                self._emit_progress(
                    "model-call",
                    "Repair Model returned an invalid response.",
                    status="error",
                    phase=Phase.DISPATCH,
                )
                raise
            else:
                self._emit_progress(
                    "model-call",
                    "Repair Model response received "
                    f"in {time.monotonic() - started_at:.1f}s.",
                    status="completed",
                    phase=Phase.DISPATCH,
                    raw_text=response,
                )
                return response

        raise AssertionError("bounded model retry loop exhausted without a result")

    @staticmethod
    def _transport_failure_summary(error: LLMTransportError) -> str:
        detail = str(error).lower()
        if "timed out" in detail or "timeout" in detail:
            return "Repair Model request timed out before the response was complete."
        if "http error 401" in detail or "http error 403" in detail:
            return "Repair Model authentication was rejected. Check SAFEFIX_REPAIR_API_KEY."
        if "http error 429" in detail:
            return "Repair Model request was rate limited (HTTP 429)."
        for status_code in (400, 404, 413, 422, 500, 502, 503, 504):
            if f"http error {status_code}" in detail:
                return f"Repair Model request failed (HTTP {status_code})."
        return "Repair Model request failed due to a network error."

    @staticmethod
    def _startup_failure_summary(reason: StopReason) -> str:
        if reason is StopReason.CONFIG_ERROR:
            return "SafeFix configuration or baseline test discovery failed."
        if reason is StopReason.TEST_PREPARATION_ERROR:
            return "SafeFix test preparation failed."
        return "SafeFix could not run the baseline test set."

    def _finalize(self, stop_reason: StopReason) -> SessionResult:
        self._phase = Phase.STOP
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
        self._last_result = result
        return result

    def _restore_best(self) -> None:
        assert self.state is not None
        assert self.snapshot_store is not None
        self.snapshot_store.restore()
        self.state.F = self.state.U_best

    def _emit(self, event: str, *, payload: dict[str, object] | None = None) -> None:
        self._emit_event("control", event, payload=payload)

    def _emit_progress(
        self,
        kind: str,
        summary: str,
        *,
        status: str,
        phase: Phase,
        raw_text: str | None = None,
    ) -> None:
        if self._progress_events:
            self._emit_event(kind, summary, status=status, phase=phase, raw_text=raw_text)

    def _emit_event(
        self,
        kind: str,
        summary: str,
        *,
        status: str | None = None,
        phase: Phase | None = None,
        payload: dict[str, object] | None = None,
        raw_text: str | None = None,
    ) -> None:
        if self._event_sink is None:
            return
        safe_payload = {"summary": summary}
        if status is not None:
            safe_payload["status"] = status
        if payload is not None:
            safe_payload.update(payload)
        emit = getattr(self._event_sink, "emit", None)
        if callable(emit):
            event = SessionEvent(
                    sequence=self._event_sequence,
                    timestamp=datetime.now(timezone.utc).isoformat().replace(
                        "+00:00", "Z"
                    ),
                    phase=self._phase if phase is None else phase,
                    kind=kind,
                    safe_payload=safe_payload,
                    raw_text=raw_text,
                )
            if self.state is not None:
                self.state.record_event(event)
            emit(event)
            self._event_sequence += 1
            return
        if callable(self._event_sink):
            self._event_sink(summary)

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
