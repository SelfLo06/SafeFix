from __future__ import annotations

import asyncio
import queue
import threading
import time

import pytest

from safefix.events import SessionEvent
from safefix.llm.base import LLMTransportError
from safefix.models import Phase, SessionResult, StopReason
from safefix.operator import OperatorCommand, OperatorCommandQueue
from safefix.tui import GuidedRepairConsole, TerminalCapabilities, TuiEventSink, render_event
from tests.fixtures.tui.fake_terminal import FakeConsole, FakePromptSession, FakeTickSource


class FakeController:
    def __init__(self) -> None:
        self.tool_calls: tuple[object, ...] = ()

    def run(self) -> SessionResult:
        return SessionResult(StopReason.REQUESTED)


class PendingPrompt:
    def __init__(self) -> None:
        self.started = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending: asyncio.Future[None] | None = None

    async def prompt_async(self, _prompt: str) -> str:
        self._loop = asyncio.get_running_loop()
        self._pending = self._loop.create_future()
        self.started.set()
        await self._pending
        raise EOFError

    def release(self) -> None:
        assert self._loop is not None
        assert self._pending is not None
        if not self._pending.done():
            self._loop.call_soon_threadsafe(self._pending.set_result, None)


def console_with_fake_prompt(responses: list[str], *, prompt: FakePromptSession | None = None) -> GuidedRepairConsole:
    command_queue = OperatorCommandQueue()
    controller = FakeController()
    console = GuidedRepairConsole(command_queue, lambda _sink, _queue: controller, lambda: prompt or FakePromptSession(responses), FakeConsole(), TerminalCapabilities(True, True, True, False), FakeTickSource([]))
    console.fake_controller = controller
    return console


def test_tui_event_sink_forwards_typed_events_to_thread_safe_queue() -> None:
    events: queue.Queue[SessionEvent] = queue.Queue()
    event = SessionEvent(1, "2026-08-07T00:00:00Z", Phase.READY, "control", {"summary": "ready"})
    TuiEventSink(events).emit(event)
    assert events.get_nowait() == event


def test_console_queues_explicit_guidance_and_controls_without_tool_dispatch() -> None:
    console = console_with_fake_prompt(["/guide", "preserve parse return type", "/stop"])
    console.run()
    assert console.command_queue.drain_ready_guidance() == ("preserve parse return type",)
    assert console.command_queue.drain_ready_commands() == (OperatorCommand("stop"),)
    assert console.fake_controller.tool_calls == ()


def test_background_event_preserves_pending_prompt_buffer() -> None:
    prompt = FakePromptSession(pending_text="keep public API")
    console = console_with_fake_prompt([], prompt=prompt)
    console.publish(SessionEvent(1, "2026-08-07T00:00:00Z", Phase.READY, "guardrail", {"summary": "safe"}))
    console.drain_events_once()
    assert prompt.pending_text == "keep public API"


def test_default_input_explains_latest_safe_status_without_queueing_guidance() -> None:
    console = console_with_fake_prompt([])
    console._input_mode = "explain"
    console.publish(SessionEvent(1, "2026-08-07T00:00:00Z", Phase.DISPATCH, "model-call", {"summary": "request in progress", "status": "running"}))
    console.drain_events_once()

    console._submit_input("why is this taking time")

    assert console.command_queue.drain_ready_guidance() == ()
    assert any("最多可能需要 120 秒" in str(line) for line, _style in console._console.lines)


def test_console_starts_in_tests_mode_and_switches_to_guide_mode() -> None:
    console = console_with_fake_prompt([])

    assert console.input_mode == "tests"
    assert console.prompt_text == "[tests] > "

    console._submit_input("/guide")
    assert console.input_mode == "guide"
    assert console.prompt_text == "[guide] > "

    console._submit_input("preserve the public API")
    assert console.command_queue.drain_ready_guidance() == ("preserve the public API",)


def test_review_mode_accepts_bare_on_and_switches_to_explain() -> None:
    class ReviewController:
        state = None

        def __init__(self) -> None:
            self.review_values: list[bool] = []

        def configure_preflight(self, *, review: bool) -> None:
            self.review_values.append(review)

    console = console_with_fake_prompt([])
    controller = ReviewController()
    console._controller = controller  # type: ignore[assignment]
    console._input_mode = "review"
    console._review_event = asyncio.Event()

    console._submit_input("on")

    assert controller.review_values == [True]
    assert console.input_mode == "explain"
    assert console._review_event.is_set()


def test_review_mode_accepts_review_prefix_without_slash() -> None:
    class ReviewController:
        state = None

        def __init__(self) -> None:
            self.review_values: list[bool] = []

        def configure_preflight(self, *, review: bool) -> None:
            self.review_values.append(review)

    console = console_with_fake_prompt([])
    controller = ReviewController()
    console._controller = controller  # type: ignore[assignment]
    console._input_mode = "review"
    console._review_event = asyncio.Event()

    console._submit_input("review off")

    assert controller.review_values == [False]
    assert console.input_mode == "explain"


def test_review_mode_rejects_invalid_input_without_advancing() -> None:
    class ReviewController:
        state = None

        def configure_preflight(self, *, review: bool) -> None:
            raise AssertionError("invalid review input must not configure review")

    console = console_with_fake_prompt([])
    console._controller = ReviewController()  # type: ignore[assignment]
    console._input_mode = "review"
    console._review_event = asyncio.Event()

    console._submit_input("maybe")

    assert console.input_mode == "review"
    assert not console._review_event.is_set()
    assert any("请输入 on 或 off" in str(line) for line, _style in console._console.lines)


def test_preflight_approval_commands_resolve_the_controller_directly() -> None:
    class ApprovalController:
        state = None

        def __init__(self) -> None:
            self.pending_approval = True
            self.approved = 0

        def approve_pending(self) -> bool:
            self.approved += 1
            self.pending_approval = False
            return True

    console = console_with_fake_prompt([])
    controller = ApprovalController()
    console._controller = controller  # type: ignore[assignment]

    console._submit_input("/approve")

    assert controller.approved == 1
    assert console.command_queue.drain_ready_commands() == ()


def test_review_mode_instruction_explains_the_available_choices_in_chinese() -> None:
    console = console_with_fake_prompt([])
    console._input_mode = "review"

    console._submit_input("maybe")

    output = "\n".join(str(line) for line, _style in console._console.lines)
    assert "on 表示修复成功后调用检查模型复核补丁" in output
    assert "off 表示跳过最终检查" in output


def test_render_event_localizes_final_review_summary() -> None:
    event = SessionEvent(
        1,
        "2026-08-07T00:00:00Z",
        Phase.FINAL_REVIEW,
        "review",
        {"summary": "Final Review pass: 修复已通过验证。"},
    )

    rendered = render_event(event, TerminalCapabilities(True, True, True, False))

    assert rendered.text == "● REVIEW 最终检查通过：修复已通过验证。"


def test_generated_baseline_failure_explains_how_to_retry() -> None:
    console = console_with_fake_prompt([])

    message = console._preflight_failure_text(
        StopReason.TEST_PREPARATION_ERROR,
        "响应不是候选测试 JSON。",
    )

    assert "没有产生可接受的测试" in message
    assert "/logs on" in message
    assert "重新选择 /tests" in message
    assert "响应不是候选测试 JSON。" in message


def test_test_model_preflight_detail_is_not_presented_as_a_configuration_error() -> None:
    console = console_with_fake_prompt([])

    message = console._preflight_failure_text(
        StopReason.CONFIG_ERROR,
        "测试模型未返回候选测试：JSON 的 candidates 数组为空。",
    )

    assert "项目配置、凭据或 pytest 测试发现失败" not in message
    assert "测试模型未返回候选测试" in message
    assert "SAFEFIX_TEST_API_KEY" not in message
    assert "/logs on" in message


def test_test_model_authentication_failure_names_only_its_role_credential() -> None:
    console = console_with_fake_prompt([])

    message = console._preflight_failure_text(
        StopReason.TEST_PREPARATION_ERROR,
        "测试模型认证被拒绝。请检查 SAFEFIX_TEST_API_KEY。",
    )

    assert "SAFEFIX_TEST_API_KEY" in message
    assert "SAFEFIX_REPAIR_API_KEY" not in message
    assert "SAFEFIX_REVIEW_API_KEY" not in message


def test_missing_preparation_diagnostic_is_not_presented_as_project_configuration_error() -> None:
    console = console_with_fake_prompt([])

    message = console._preflight_failure_text(
        StopReason.CONFIG_ERROR,
        "测试准备服务未提供失败原因。这是 SafeFix 内部诊断错误；请执行 /logs on 后重试。",
    )

    assert "SafeFix 内部诊断错误" in message
    assert "项目配置、凭据或 pytest 测试发现失败" not in message
    assert "/logs on" in message


def test_generated_only_with_existing_tests_names_the_available_test_sources() -> None:
    console = console_with_fake_prompt([])

    message = console._preflight_failure_text(
        StopReason.CONFIG_ERROR,
        "已检测到可收集的已有测试，不能使用 generated-only；请选择 existing 或 mixed。",
    )

    assert "/tests existing" in message
    assert "/tests mixed" in message
    assert "项目配置、凭据或 pytest 测试发现失败" not in message


def test_tests_mode_accepts_mix_as_mixed() -> None:
    class TestController:
        state = None

        def __init__(self) -> None:
            self.test_values: list[str] = []

        def configure_preflight(self, *, tests: str) -> None:
            self.test_values.append(tests)

    console = console_with_fake_prompt([])
    controller = TestController()
    console._controller = controller  # type: ignore[assignment]
    console._input_mode = "tests"
    console._baseline_event = asyncio.Event()

    console._submit_input("mix")

    assert controller.test_values == ["mixed"]
    assert console._baseline_event.is_set()


def test_explain_text_is_not_repair_guidance_and_control_keeps_mode() -> None:
    console = console_with_fake_prompt([])
    console._input_mode = "explain"
    console._submit_input("why did the candidate roll back?")
    console._submit_input("/stop")

    assert console.input_mode == "explain"
    assert console.command_queue.drain_ready_guidance() == ()
    assert console.command_queue.drain_ready_commands() == (OperatorCommand("stop"),)


def test_explain_text_queues_a_read_only_question_while_runner_is_active() -> None:
    console = console_with_fake_prompt([])
    console._input_mode = "explain"
    console._controller_active = True

    console._submit_input("why did the candidate roll back?")

    assert console.command_queue.drain_ready_explanations() == (
        "why did the candidate roll back?",
    )
    assert console.command_queue.drain_ready_guidance() == ()


def test_logs_toggle_is_view_state_and_does_not_enter_operator_queue() -> None:
    console = console_with_fake_prompt([])
    console._input_mode = "explain"

    console._submit_input("/logs on")
    assert console.raw_logs_enabled is True
    console._submit_input("/logs off")

    assert console.raw_logs_enabled is False
    assert console.command_queue.drain_ready_commands() == ()


def test_completed_explain_transport_failure_is_rendered_without_escaping() -> None:
    class CompletedController:
        state = object()

        def answer_explanation(self, _question: str) -> str:
            raise LLMTransportError("timed out")

    console = console_with_fake_prompt([])
    console._input_mode = "explain"
    console._controller = CompletedController()  # type: ignore[assignment]
    console._submit_input("what happened?")

    assert any("说明请求失败" in str(line) for line, _style in console._console.lines)


def test_guide_command_queues_explicit_repair_guidance() -> None:
    console = console_with_fake_prompt([])

    console._submit_input("/guide")
    console._submit_input("preserve the public API")

    assert console.command_queue.drain_ready_guidance() == ("preserve the public API",)


def test_eof_queues_safe_stop_only_while_controller_is_active() -> None:
    console = console_with_fake_prompt([])
    console._controller_active = True
    asyncio.run(console._read_input())
    assert console.command_queue.drain_ready_commands() == (OperatorCommand("stop"),)


def test_terminal_close_queues_safe_stop_only_while_controller_is_active() -> None:
    class ClosingPrompt:
        async def prompt_async(self, _prompt: str) -> str:
            raise OSError("terminal closed")

    console = console_with_fake_prompt([], prompt=ClosingPrompt())
    console._controller_active = True
    asyncio.run(console._read_input())
    assert console.command_queue.drain_ready_commands() == (OperatorCommand("stop"),)


def test_console_drains_worker_events_while_prompt_is_pending() -> None:
    rendered = threading.Event()
    event = SessionEvent(1, "2026-08-07T00:00:00Z", Phase.READY, "guardrail", {"summary": "safe"})

    class EventController:
        def run(self) -> SessionResult:
            sink.emit(event)
            rendered.wait()
            return SessionResult(StopReason.REQUESTED)

    prompt = PendingPrompt()
    command_queue = OperatorCommandQueue()
    console_output = FakeConsole()
    sink: TuiEventSink

    def controller_factory(event_sink: TuiEventSink, _queue: OperatorCommandQueue) -> EventController:
        nonlocal sink
        sink = event_sink
        return EventController()

    console = GuidedRepairConsole(
        command_queue,
        controller_factory,
        lambda: prompt,
        console_output,
        TerminalCapabilities(True, True, True, False),
        FakeTickSource([]),
    )

    worker = threading.Thread(target=console.run, daemon=True)
    worker.start()
    assert prompt.started.wait(timeout=0.5)
    try:
        deadline = time.monotonic() + 0.5
        while not any(line[0] == "● SAFE safe" for line in console_output.lines):
            assert time.monotonic() < deadline
            time.sleep(0.01)
        rendered.set()
    finally:
        rendered.set()
        prompt.release()
        worker.join(timeout=1)


def test_console_keeps_prompt_open_after_runner_finishes_until_operator_exits() -> None:
    prompt = PendingPrompt()
    console = console_with_fake_prompt([], prompt=prompt)

    result: list[SessionResult] = []
    worker = threading.Thread(target=lambda: result.append(console.run()), daemon=True)
    worker.start()
    assert prompt.started.wait(timeout=0.5)
    try:
        worker.join(timeout=0.5)
        assert worker.is_alive()
    finally:
        prompt.release()
        worker.join(timeout=0.5)

    assert result == [SessionResult(StopReason.REQUESTED)]


def test_non_interactive_console_reraises_controller_exception_after_cleanup() -> None:
    failure = RuntimeError("runner failed")

    class RaisingController:
        def run(self) -> SessionResult:
            raise failure

    console = GuidedRepairConsole(
        OperatorCommandQueue(),
        lambda _sink, _queue: RaisingController(),
        FakePromptSession,
        FakeConsole(),
        TerminalCapabilities(False, False, False, False),
        FakeTickSource([]),
    )

    with pytest.raises(RuntimeError) as raised:
        console.run()

    assert raised.value is failure


def test_interactive_console_reraises_controller_exception_after_cleanup() -> None:
    failure = RuntimeError("runner failed")

    class RaisingController:
        def run(self) -> SessionResult:
            raise failure

    console = GuidedRepairConsole(
        OperatorCommandQueue(),
        lambda _sink, _queue: RaisingController(),
        FakePromptSession,
        FakeConsole(),
        TerminalCapabilities(True, True, True, False),
        FakeTickSource([]),
    )

    with pytest.raises(RuntimeError) as raised:
        console.run()

    assert raised.value is failure
