from pathlib import Path

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
