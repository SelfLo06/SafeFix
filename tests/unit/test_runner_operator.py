from pathlib import Path
import threading
import time

from safefix.events import SessionEvent
from safefix.junit import TestCaseResult as _TestCaseResult
from safefix.llm.mock import MockLLM
from safefix.models import BaselineSource, Config, FailureSet, GuardDecision, StopReason
from safefix.operator import OperatorCommandQueue
from safefix.session_setup import manifest_from_entries
from safefix.test_manifest import ManifestEntry
from safefix.testprep.service import PreparationResult, PreparationSummary
from safefix.testrunner import TestRunResult as _TestRunResult


class FakeCredentials:
    def get(self) -> str:
        return "repair-key"


def _case(failure_id: str, status: str = "failed") -> _TestCaseResult:
    classname, name = failure_id.split("::", 1)
    return _TestCaseResult(failure_id, classname, name, status)


def _report(*failure_ids: str, exit_code: int = 1) -> _TestRunResult:
    cases = tuple(_case(failure_id) for failure_id in failure_ids)
    return _TestRunResult(exit_code=exit_code, cases=cases, valid=True)


def _legacy_project(tmp_path: Path) -> None:
    (tmp_path / "safefix.toml").write_text(
        'base_url = "https://example.invalid"\nmodel = "repair-model"\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")


class SequentialRunner:
    def __init__(self, results: list[_TestRunResult]) -> None:
        self._results = iter(results)

    def run(self) -> _TestRunResult:
        return next(self._results)


class TypedEvents:
    def __init__(self) -> None:
        self.events: list[SessionEvent] = []
        self.changed = threading.Event()

    def emit(self, event: SessionEvent) -> None:
        self.events.append(event)
        self.changed.set()

    def wait_for(self, command: str) -> SessionEvent:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            for event in self.events:
                if event.safe_payload.get("command") == command:
                    return event
            self.changed.wait(0.05)
            self.changed.clear()
        raise AssertionError(f"event {command!r} was not emitted")


def _run_async(runner: object) -> tuple[threading.Thread, dict[str, object]]:
    result: dict[str, object] = {}

    def run() -> None:
        result["value"] = runner.run()  # type: ignore[attr-defined]

    thread = threading.Thread(target=run)
    thread.start()
    return thread, result


def test_guidance_queued_during_llm_call_appears_only_in_next_prompt(tmp_path: Path) -> None:
    _legacy_project(tmp_path)
    queue = OperatorCommandQueue()

    class BlockingLLM:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def complete(self, prompt: str) -> str:
            self.prompts.append(prompt)
            if len(self.prompts) == 1:
                queue.submit_text("preserve the public return type")
                return '{"tool": "read_file", "path": "src/app.py"}'
            return '{"tool": "finish"}'

    llm = BlockingLLM()
    from safefix.runner import SessionRunner
    test_runner = SequentialRunner([_report("tests.app::test_value")])

    runner = SessionRunner(
        tmp_path,
        credentials=FakeCredentials(),
        llm_client=llm,
        operator_queue=queue,
        test_runner_factory=lambda _root, _args: test_runner,
    )

    result = runner.run()

    assert result.stop_reason is StopReason.REQUESTED
    assert len(llm.prompts) == 2
    assert "preserve the public return type" not in llm.prompts[0]
    assert "preserve the public return type" in llm.prompts[1]
    assert runner.state is not None
    assert runner.state.guidance_event_summaries == ("preserve the public return type",)


def test_pause_gates_next_repair_decision_until_resume(tmp_path: Path) -> None:
    _legacy_project(tmp_path)
    queue = OperatorCommandQueue()
    queue.submit_text("/pause")
    events = TypedEvents()

    class CountingLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, prompt: str) -> str:
            del prompt
            self.calls += 1
            return '{"tool": "finish"}'

    llm = CountingLLM()
    from safefix.runner import SessionRunner

    runner = SessionRunner(
        tmp_path,
        credentials=FakeCredentials(),
        llm_client=llm,
        operator_queue=queue,
        event_sink=events,
        test_runner_factory=lambda _root, _args: SequentialRunner(
            [_report("tests.app::test_value")]
        ),
    )

    thread, result = _run_async(runner)
    events.wait_for("pause")
    assert runner.phase.value == "paused"
    assert llm.calls == 0
    assert "value" not in result

    queue.submit_text("/resume")
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert llm.calls == 1
    assert result["value"].stop_reason is StopReason.REQUESTED  # type: ignore[union-attr]
    assert runner.phase.value == "stop"


def test_status_snapshot_is_bounded_redacted_and_state_immutable(tmp_path: Path) -> None:
    _legacy_project(tmp_path)
    queue = OperatorCommandQueue()
    queue.submit_text("/pause")
    events = TypedEvents()

    class CountingLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, prompt: str) -> str:
            del prompt
            self.calls += 1
            return '{"tool": "finish"}'

    llm = CountingLLM()
    failure_ids = ("tests.app::aaa_tokenSecret",) + tuple(
        f"tests.app::test_{index}" for index in range(24)
    )

    from safefix.runner import SessionRunner

    runner = SessionRunner(
        tmp_path,
        credentials=FakeCredentials(),
        llm_client=llm,
        operator_queue=queue,
        event_sink=events,
        test_runner_factory=lambda _root, _args: SequentialRunner(
            [_report(*failure_ids)]
        ),
    )
    thread, result = _run_async(runner)
    events.wait_for("pause")
    assert "value" not in result
    assert runner.state is not None
    before = (
        runner.state.steps,
        runner.state.rounds,
        runner.state.no_progress_rounds,
        runner.state.F,
        runner.state.U_best,
        runner.pending_approval,
    )

    queue.submit_text("/status")
    event = events.wait_for("status")
    payload = event.safe_payload
    assert event.phase.value == "paused"
    assert payload["status"] == "snapshot"
    assert payload["phase"] == "paused"
    assert payload["step"] == 0
    assert payload["round"] == 0
    assert payload["no_progress"] == 0
    assert len(payload["unresolved_failures"]) == 20  # type: ignore[arg-type]
    assert len(payload["best_checkpoint"]["failure_ids"]) == 20  # type: ignore[index]
    assert "aaa_tokenSecret" not in repr(payload)
    assert payload["pending_approval"] == {"pending": False, "tool": None}
    assert llm.calls == 0
    payload["phase"] = "ready"
    assert event.safe_payload["phase"] == "paused"
    assert before == (
        runner.state.steps,
        runner.state.rounds,
        runner.state.no_progress_rounds,
        runner.state.F,
        runner.state.U_best,
        runner.pending_approval,
    )

    queue.submit_text("/resume")
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert llm.calls == 1
    assert result["value"].stop_reason is StopReason.REQUESTED  # type: ignore[union-attr]


def test_stop_from_paused_is_terminal_and_does_not_dispatch_model(
    tmp_path: Path,
) -> None:
    _legacy_project(tmp_path)
    queue = OperatorCommandQueue()
    queue.submit_text("/pause")
    events = TypedEvents()

    class CountingLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, prompt: str) -> str:
            del prompt
            self.calls += 1
            return '{"tool": "finish"}'

    llm = CountingLLM()
    from safefix.runner import SessionRunner

    runner = SessionRunner(
        tmp_path,
        credentials=FakeCredentials(),
        llm_client=llm,
        operator_queue=queue,
        event_sink=events,
        test_runner_factory=lambda _root, _args: SequentialRunner(
            [_report("tests.app::test_value")]
        ),
    )
    thread, result = _run_async(runner)
    events.wait_for("pause")
    queue.submit_text("/stop")
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert llm.calls == 0
    assert result["value"].stop_reason is StopReason.OPERATOR_STOP  # type: ignore[union-attr]
    assert runner.phase.value == "stop"


def test_controls_queued_during_llm_are_ordered_at_next_ready_boundary(
    tmp_path: Path,
) -> None:
    _legacy_project(tmp_path)
    queue = OperatorCommandQueue()
    events = TypedEvents()
    entered = threading.Event()
    release = threading.Event()

    class BlockingLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, prompt: str) -> str:
            del prompt
            self.calls += 1
            if self.calls == 1:
                entered.set()
                release.wait(timeout=2)
                return '{"tool": "read_file", "path": "src/app.py"}'
            return '{"tool": "finish"}'

    llm = BlockingLLM()
    from safefix.runner import SessionRunner

    runner = SessionRunner(
        tmp_path,
        credentials=FakeCredentials(),
        llm_client=llm,
        operator_queue=queue,
        event_sink=events,
        test_runner_factory=lambda _root, _args: SequentialRunner(
            [_report("tests.app::test_value")]
        ),
    )
    thread, result = _run_async(runner)
    assert entered.wait(timeout=2)
    queue.submit_text("/pause")
    queue.submit_text("/status")
    queue.submit_text("/resume")
    release.set()
    events.wait_for("status")
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert [
        event.safe_payload["command"]
        for event in events.events
        if event.safe_payload.get("command") in {"pause", "status", "resume"}
    ] == ["pause", "status", "resume"]
    assert events.wait_for("status").phase.value == "paused"
    assert llm.calls == 2
    assert result["value"].stop_reason is StopReason.REQUESTED  # type: ignore[union-attr]


def test_controls_queued_during_apply_patch_and_pytest_wait_for_ready(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _legacy_project(tmp_path)
    queue = OperatorCommandQueue()
    events = TypedEvents()
    from safefix.runner import SessionRunner
    import safefix.runner as runner_module

    original_dispatch = runner_module.dispatch
    dispatch_entered = threading.Event()
    dispatch_enqueued = threading.Event()
    release_dispatch = threading.Event()

    def blocked_dispatch(root: Path, action: object, snapshot: object) -> object:
        if getattr(action, "tool", None).value == "apply_patch":
            dispatch_entered.set()
            queue.submit_text("/pause")
            dispatch_enqueued.set()
            if not release_dispatch.wait(timeout=2):
                raise AssertionError("dispatch gate was not released")
        return original_dispatch(root, action, snapshot)

    monkeypatch.setattr(runner_module, "dispatch", blocked_dispatch)

    class QueueingEvaluationRunner(SequentialRunner):
        def __init__(self) -> None:
            super().__init__(
                [
                    _report("tests.app::test_value", "tests.app::test_other"),
                    _report("tests.app::test_value"),
                ]
            )
            self.evaluations = 0
            self.evaluation_entered = threading.Event()
            self.evaluation_enqueued = threading.Event()
            self.release_evaluation = threading.Event()

        def run(self) -> _TestRunResult:
            self.evaluations += 1
            if self.evaluations == 2:
                self.evaluation_entered.set()
                queue.submit_text("/status")
                self.evaluation_enqueued.set()
                if not self.release_evaluation.wait(timeout=2):
                    raise AssertionError("evaluation gate was not released")
            return super().run()

    test_runner = QueueingEvaluationRunner()

    class PatchThenFinishLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, prompt: str) -> str:
            del prompt
            self.calls += 1
            if self.calls == 1:
                return (
                    '{"tool": "apply_patch", "changes": [{"path": "src/app.py", '
                    '"old_text": "value = 1", "new_text": "value = 2"}]}'
                )
            return '{"tool": "finish"}'

    runner = SessionRunner(
        tmp_path,
        credentials=FakeCredentials(),
        llm_client=PatchThenFinishLLM(),
        operator_queue=queue,
        event_sink=events,
        test_runner_factory=lambda _root, _args: test_runner,
    )
    thread, result = _run_async(runner)
    assert dispatch_entered.wait(timeout=2)
    assert dispatch_enqueued.wait(timeout=2)
    try:
        assert events.events == []
        assert runner.phase.value == "ready"
        assert "value" not in result
    finally:
        release_dispatch.set()

    assert test_runner.evaluation_entered.wait(timeout=2)
    assert test_runner.evaluation_enqueued.wait(timeout=2)
    try:
        assert events.events == []
        assert runner.phase.value == "ready"
        assert "value" not in result
    finally:
        test_runner.release_evaluation.set()

    events.wait_for("status")
    assert runner.phase.value == "paused"
    assert runner.state is not None
    assert runner.state.rounds == 1
    assert runner.state.steps == 1
    assert runner.state.F == runner.state.U_best
    assert runner.state.U_best == FailureSet(frozenset({"tests.app::test_value"}))
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "value = 2\n"

    queue.submit_text("/resume")
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert result["value"].stop_reason is StopReason.REQUESTED  # type: ignore[union-attr]


def test_pause_preserves_pending_approval_and_deny_stays_scoped(tmp_path: Path) -> None:
    _legacy_project(tmp_path)
    queue = OperatorCommandQueue()
    events = TypedEvents()

    class ApprovalGuardrail:
        def check(self, action: object) -> GuardDecision:
            from safefix.models import ToolCall, ToolName

            return (
                GuardDecision.REQUIRE_APPROVAL
                if isinstance(action, ToolCall) and action.tool is ToolName.APPLY_PATCH
                else GuardDecision.ALLOW
            )

    class PatchThenFinishLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, prompt: str) -> str:
            del prompt
            self.calls += 1
            if self.calls == 1:
                queue.submit_text("/pause")
                return (
                    '{"tool": "apply_patch", "changes": [{"path": "src/app.py", '
                    '"old_text": "value = 1", "new_text": "value = 2"}]}'
                )
            return '{"tool": "finish"}'

    from safefix.runner import SessionRunner

    runner = SessionRunner(
        tmp_path,
        credentials=FakeCredentials(),
        llm_client=PatchThenFinishLLM(),
        operator_queue=queue,
        event_sink=events,
        guardrail=ApprovalGuardrail(),
        test_runner_factory=lambda _root, _args: SequentialRunner(
            [_report("tests.app::test_value")]
        ),
    )
    thread, result = _run_async(runner)
    events.wait_for("pause")
    assert runner.phase.value == "paused"
    assert runner.pending_approval
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "value = 1\n"

    queue.submit_text("/status")
    status = events.wait_for("status")
    assert status.safe_payload["pending_approval"] == {
        "pending": True,
        "tool": "apply_patch",
    }

    queue.submit_text("/deny")
    deadline = time.monotonic() + 2
    while runner.pending_approval and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not runner.pending_approval
    assert runner.phase.value == "paused"
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "value = 1\n"

    queue.submit_text("/resume")
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert not runner.pending_approval
    assert runner.state is not None
    assert runner.state.rounds == 0
    assert runner.state.steps == 2
    assert result["value"].stop_reason is StopReason.REQUESTED  # type: ignore[union-attr]


def test_operator_stop_after_better_patch_restores_best_and_is_not_success(
    tmp_path: Path,
) -> None:
    _legacy_project(tmp_path)
    queue = OperatorCommandQueue()

    class PatchThenStopLLM:
        def complete(self, prompt: str) -> str:
            del prompt
            queue.submit_text("/stop")
            return (
                '{"tool": "apply_patch", "changes": [{"path": "src/app.py", '
                '"old_text": "value = 1", "new_text": "value = 2"}]}'
            )

    from safefix.runner import SessionRunner
    test_runner = SequentialRunner(
        [_report("tests.app::test_a", "tests.app::test_b"), _report("tests.app::test_a")]
    )

    runner = SessionRunner(
        tmp_path,
        credentials=FakeCredentials(),
        llm_client=PatchThenStopLLM(),
        operator_queue=queue,
        test_runner_factory=lambda _root, _args: test_runner,
    )

    result = runner.run()

    assert result.stop_reason is StopReason.OPERATOR_STOP
    assert result.stop_reason is not StopReason.SUCCESS
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "value = 2\n"


def test_approval_commands_are_pending_and_no_pending_commands_emit_typed_event(
    tmp_path: Path,
) -> None:
    _legacy_project(tmp_path)
    queue = OperatorCommandQueue()
    queue.submit_text("/approve")
    queue.submit_text("/deny")
    events: list[SessionEvent] = []

    class ApprovalGuardrail:
        def check(self, action: object) -> GuardDecision:
            del action
            return GuardDecision.ALLOW

    class TypedSink:
        def emit(self, event: SessionEvent) -> None:
            events.append(event)

    from safefix.runner import SessionRunner
    test_runner = SequentialRunner([_report("tests.app::test_value")])

    runner = SessionRunner(
        tmp_path,
        credentials=FakeCredentials(),
        llm_client=MockLLM(['{"tool": "finish"}']),
        operator_queue=queue,
        event_sink=TypedSink(),
        guardrail=ApprovalGuardrail(),
        test_runner_factory=lambda _root, _args: test_runner,
    )

    result = runner.run()

    assert result.stop_reason is StopReason.REQUESTED
    assert any(
        event.kind == "control"
        and event.safe_payload.get("command") == "approve"
        and event.safe_payload.get("status") == "ignored"
        for event in events
    )
    assert any(
        event.kind == "control"
        and event.safe_payload.get("command") == "deny"
        and event.safe_payload.get("status") == "ignored"
        for event in events
    )


def test_pending_patch_waits_for_queued_approval_before_evaluation(tmp_path: Path) -> None:
    _legacy_project(tmp_path)
    queue = OperatorCommandQueue()

    class ApprovalGuardrail:
        def check(self, action: object) -> GuardDecision:
            from safefix.models import ToolCall, ToolName

            return (
                GuardDecision.REQUIRE_APPROVAL
                if isinstance(action, ToolCall) and action.tool is ToolName.APPLY_PATCH
                else GuardDecision.ALLOW
            )

    class PatchThenApproveLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, prompt: str) -> str:
            del prompt
            self.calls += 1
            if self.calls == 1:
                queue.submit_text("/approve")
                return (
                    '{"tool": "apply_patch", "changes": [{"path": "src/app.py", '
                    '"old_text": "value = 1", "new_text": "value = 2"}]}'
                )
            return '{"tool": "finish"}'

    from safefix.runner import SessionRunner

    test_runner = SequentialRunner(
        [_report("tests.app::test_value"), _report("tests.app::test_value")]
    )
    runner = SessionRunner(
        tmp_path,
        credentials=FakeCredentials(),
        llm_client=PatchThenApproveLLM(),
        operator_queue=queue,
        guardrail=ApprovalGuardrail(),
        test_runner_factory=lambda _root, _args: test_runner,
    )

    result = runner.run()

    assert result.stop_reason is StopReason.REQUESTED
    assert result.rounds == 1
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "value = 1\n"


def test_frozen_manifest_is_used_for_every_patch_evaluation_and_new_failures_stay_out_of_f0(
    tmp_path: Path,
) -> None:
    _legacy_project(tmp_path)
    test_path = "tests/test_existing.py"
    (tmp_path / "tests").mkdir()
    (tmp_path / test_path).write_text("def test_existing():\n    assert False\n", encoding="utf-8")
    f0 = "tests.test_existing::test_existing"
    new_failure = "tests.test_existing::test_new_regression"
    baseline = _report(f0)
    evaluation = _report(f0, new_failure)
    target_calls: list[tuple[str, ...]] = []

    class V2Runner:
        def __init__(self, target_paths: tuple[str, ...], allow_empty: bool) -> None:
            self.target_paths = target_paths
            self.allow_empty = allow_empty

        def run(self) -> _TestRunResult:
            target_calls.append(self.target_paths)
            if self.target_paths:
                return baseline if target_calls.count((test_path,)) == 1 else evaluation
            return _TestRunResult(0, (_case(f0, "failed"),), valid=True)

        def collect_test_paths(self) -> tuple[str, ...]:
            return (test_path,)

    def runner_factory(
        _root: Path,
        _args: list[str],
        *,
        target_paths: tuple[str, ...],
        allow_empty: bool,
    ) -> V2Runner:
        return V2Runner(tuple(target_paths), allow_empty)

    def preparation_factory(_request: object) -> PreparationResult:
        return PreparationResult(
            (ManifestEntry(test_path, "0" * 64, BaselineSource.EXISTING),),
            PreparationSummary(BaselineSource.EXISTING, existing_test_count=1),
        )

    class CapturingLLM:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def complete(self, prompt: str) -> str:
            self.prompts.append(prompt)
            if len(self.prompts) == 1:
                return (
                    '{"tool": "apply_patch", "changes": [{"path": "src/app.py", '
                    '"old_text": "value = 1", "new_text": "value = 2"}]}'
                )
            return '{"tool": "finish"}'

    llm = CapturingLLM()
    from safefix.runner import SessionRunner

    runner = SessionRunner(
        tmp_path,
        credentials=FakeCredentials(),
        llm_client=llm,
        test_runner_factory=runner_factory,
        preparation_factory=preparation_factory,
        manifest_factory=manifest_from_entries,
        config_loader=lambda *_args, **_kwargs: Config(
            base_url="https://example.invalid",
            model="repair-model",
        ),
    )

    result = runner.run()

    assert result.stop_reason is StopReason.REQUESTED
    assert target_calls == [(), (test_path,), (test_path,)]
    assert runner.state is not None
    assert runner.state.F0 == FailureSet(frozenset({f0}))
    assert new_failure not in runner.state.F0.ids
    assert new_failure not in llm.prompts[-1]
