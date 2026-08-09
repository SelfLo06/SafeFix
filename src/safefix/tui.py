"""Presentation-only guided repair console for interactive terminals."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
import queue
import re
import threading
import time
from typing import Protocol, cast

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console

from .events import SessionEvent
from .models import SessionResult, StopReason
from .operator import OperatorCommandQueue
from .runner import SessionRunner


@dataclass(frozen=True)
class TerminalCapabilities:
    interactive: bool
    color: bool
    unicode: bool
    animation: bool


def terminal_capabilities(
    stdout: object,
    stderr: object,
    environ: Mapping[str, str],
    no_animation: bool,
    test_mode: bool,
) -> TerminalCapabilities:
    interactive = bool(stdout.isatty()) and bool(stderr.isatty())  # type: ignore[attr-defined]
    dumb_terminal = environ.get("TERM") == "dumb"
    color = interactive and not dumb_terminal and "NO_COLOR" not in environ
    unicode = interactive and not dumb_terminal
    animation = interactive and color and unicode and not no_animation and not test_mode
    return TerminalCapabilities(interactive, color, unicode, animation)


class TuiEventSink:
    """Move already-sanitized runner events across the worker/UI boundary."""

    def __init__(
        self,
        events: queue.Queue[SessionEvent],
        on_emit: Callable[[], None] | None = None,
    ) -> None:
        self._events = events
        self._on_emit = on_emit

    def emit(self, event: SessionEvent) -> None:
        self._events.put(event)
        if self._on_emit is not None:
            self._on_emit()


@dataclass(frozen=True)
class RenderedTranscriptEntry:
    text: str
    style: str


_EVENT_LABELS = {
    "pytest": "TEST",
    "guardrail": "SAFE",
    "patch": "PATCH",
    "tool": "TOOL",
    "model-call": "MODEL",
    "control": "CONTROL",
    "guidance": "GUIDE",
    "explain": "EXPLAIN",
    "review": "REVIEW",
    "stability-run": "TEST",
    "approval": "SAFE",
    "terminal": "CONTROL",
}
_EVENT_STYLES = {
    "pytest": "cyan",
    "guardrail": "green",
    "patch": "yellow",
    "tool": "cyan",
    "model-call": "green",
    "control": "green",
    "guidance": "green",
    "explain": "green",
    "review": "yellow",
    "stability-run": "cyan",
    "approval": "yellow",
    "terminal": "green",
}


def render_event(
    event: SessionEvent, capabilities: TerminalCapabilities
) -> RenderedTranscriptEntry:
    """Project the safe summary into one scrollback transcript line."""
    summary = _localized_summary(event.kind, str(event.safe_payload.get("summary", event.kind)))
    label = _EVENT_LABELS.get(event.kind, event.kind.upper())
    marker = "● " if capabilities.unicode else "["
    suffix = " " if capabilities.unicode else "] "
    return RenderedTranscriptEntry(
        text=f"{marker}{label}{suffix}{summary}",
        style=_EVENT_STYLES.get(event.kind, "white") if capabilities.color else "",
    )


def _localized_summary(kind: str, summary: str) -> str:
    """Translate Harness event summaries at the terminal presentation boundary."""
    if kind == "pytest":
        if summary == "Preparing and running the baseline test set.":
            return "正在准备并运行基线测试。"
        match = re.fullmatch(r"Baseline ready: (\d+) failing test\(s\) frozen for repair\.", summary)
        if match:
            return f"基线已冻结：{match.group(1)} 个失败测试，等待修复。"
    if kind == "model-call":
        if summary == "Test Model request in progress.":
            return "Test Model 正在生成候选测试。"
        if summary == "Test Model response received.":
            return "Test Model 响应已收到，正在校验候选测试。"
        match = re.fullmatch(r"Repair Model response received(?: in (.+))?\.", summary)
        if match:
            suffix = f"，用时 {match.group(1)}" if match.group(1) else ""
            return f"修复模型响应已收到{suffix}。"
        if summary == "Repair Model request in progress.":
            return "修复模型请求进行中。"
    if kind == "stability-run":
        if summary == "Candidate stability verification in progress.":
            return "正在进行候选测试稳定性校验。"
        if summary == "Candidate stability verification completed.":
            return "候选测试稳定性校验完成。"
    if kind == "approval":
        if summary == "Generated failing test requires operator approval.":
            return "生成测试稳定失败，等待 /approve 采纳或 /deny 拒绝。"
        if summary == "Generated test approval resolved.":
            return "生成测试候选审批已处理。"
    if kind == "tool":
        match = re.fullmatch(r"([a-z_]+) (completed|failed)", summary)
        if match:
            outcome = "已完成" if match.group(2) == "completed" else "失败"
            return f"工具 {match.group(1)} {outcome}。"
    if kind == "review":
        if summary == "Review Model request in progress.":
            return "检查模型正在复核最终补丁。"
        if summary == "Final Review failed.":
            return "最终检查执行失败。"
        match = re.fullmatch(r"Final Review (pass|warn|review_required|not_configured): (.+)", summary, re.DOTALL)
        if match:
            verdict = {
                "pass": "通过",
                "warn": "警告",
                "review_required": "需要人工复核",
                "not_configured": "未配置",
            }[match.group(1)]
            return f"最终检查{verdict}：{match.group(2)}"
    if kind == "control":
        match = re.fullmatch(r"round outcome=(success|better|same|worse) rounds=(\d+)", summary)
        if match:
            outcome = {
                "success": "修复成功",
                "better": "已有改善",
                "same": "没有改善",
                "worse": "结果变差，已回滚",
            }[match.group(1)]
            return f"候选修复结果：{outcome}；已完成第 {match.group(2)} 轮。"
        match = re.fullmatch(r"stop reason=([a-z_]+) exit_code=(\d+)", summary)
        if match:
            return f"会话结束：{_localized_stop_reason(match.group(1))}（退出码 {match.group(2)}）。"
    return summary


def _localized_stop_reason(reason: str) -> str:
    return {
        "success": "修复成功",
        "operator_stop": "操作员请求停止",
        "requested": "请求停止",
        "error": "发生错误",
        "config_error": "配置错误",
        "test_preparation_error": "测试准备失败",
    }.get(reason, reason)


class _TickSource(Protocol):
    def next_tick(self) -> object:
        """Advance a deterministic animation clock."""


class GuidedRepairConsole:
    """Render queued events while submitting operator input to the Harness queue."""

    def __init__(
        self,
        command_queue: OperatorCommandQueue,
        controller_factory: Callable[[TuiEventSink, OperatorCommandQueue], SessionRunner],
        input_factory: Callable[[], PromptSession],
        console: Console,
        capabilities: TerminalCapabilities,
        tick_source: _TickSource,
    ) -> None:
        self.command_queue = command_queue
        self._controller_factory = controller_factory
        self._input_factory = input_factory
        self._console = console
        self._capabilities = capabilities
        self._tick_source = tick_source
        self._events: queue.Queue[SessionEvent] = queue.Queue()
        self._controller_active = False
        self._result: SessionResult | None = None
        self._failure: BaseException | None = None
        self._controller: SessionRunner | None = None
        self._activity_text = ""
        self._activity_event: SessionEvent | None = None
        self._activity_tick = 0
        self._operation_started_at: float | None = None
        self._started_at = time.monotonic()
        self._raw_logs_enabled = False
        self._raw_logs: list[str] = []
        self._raw_logs_truncated = False
        self._prompt: PromptSession | None = None
        self._last_event: SessionEvent | None = None
        self._input_mode = "tests"
        self._start_event: asyncio.Event | None = None
        self._baseline_event: asyncio.Event | None = None
        self._review_event: asyncio.Event | None = None
        self._review_selected = False
        self.rendered_event_sequences: list[int] = []

    @property
    def input_mode(self) -> str:
        return self._input_mode

    @property
    def prompt_text(self) -> str:
        return f"[{self._input_mode}] > "

    @property
    def raw_logs_enabled(self) -> bool:
        return self._raw_logs_enabled

    def publish(self, event: SessionEvent) -> None:
        self._events.put(event)

    def drain_events_once(self) -> None:
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                return
            self.rendered_event_sequences.append(event.sequence)
            self._last_event = event
            if event.raw_text is not None:
                if event.kind != "explain":
                    self.record_raw_log("[MODEL RAW RESPONSE]\n" + event.raw_text)
            entry = render_event(event, self._capabilities)
            if event.safe_payload.get("status") == "running" and self._capabilities.animation:
                self._activity_event = event
                self._operation_started_at = time.monotonic()
                self._activity_text = "running"
                self._invalidate_prompt()
                continue
            self._activity_text = ""
            self._activity_event = None
            self._operation_started_at = None
            self._invalidate_prompt()
            if event.kind == "explain" and event.safe_payload.get("status") == "completed":
                self._print_explanation(event.raw_text or str(event.safe_payload.get("summary", "")))
            else:
                self._print(entry.text, style=entry.style)

    def _print_explanation(self, response: str) -> None:
        lines: list[str] = []
        for line in response.strip().splitlines():
            text = line.strip()
            text = text.lstrip("#").strip()
            if text.startswith(("-", "*")):
                text = "• " + text[1:].strip()
            text = text.replace("**", "").replace("__", "").replace("`", "")
            if text:
                lines.append(text)
        self._print("SafeFix\n" + "\n".join(lines), style="bright_cyan")

    def _operation_text(self, event: SessionEvent) -> str:
        summary = str(event.safe_payload.get("summary", "working"))
        if event.kind == "model-call" and event.safe_payload.get("role") == "test":
            return f"正在生成测试 · {self._operation_elapsed()}"
        if event.kind == "review":
            return f"正在最终检查 · {self._operation_elapsed()}"
        if event.kind == "model-call" or summary.startswith("Repair Model request"):
            decision = 0
            if self._controller is not None and self._controller.state is not None:
                decision = self._controller.state.steps
            prefix = "正在分析"
            if decision:
                prefix += f" · 决策 {decision}"
            attempt = summary.removeprefix("Repair Model request ").removesuffix(" in progress.")
            return f"{prefix} · 请求 {attempt} · {self._operation_elapsed()}"
        if event.kind == "pytest":
            return f"正在验证 · {self._operation_elapsed()}"
        if event.kind == "stability-run":
            candidate = event.safe_payload.get("candidate_id", "候选")
            return f"正在稳定性校验 · {candidate} · {self._operation_elapsed()}"
        if event.kind == "explain":
            return f"正在分析 · 回答操作员问题 · {self._operation_elapsed()}"
        return f"正在处理 · {self._operation_elapsed()}"

    async def _prepare_preflight(
        self,
        prepare: Callable[[], SessionResult | None],
        event_ready: asyncio.Event,
    ) -> SessionResult | None:
        """Run baseline preparation off the UI loop while rendering its events."""
        task = asyncio.create_task(asyncio.to_thread(prepare))
        while not task.done():
            try:
                await asyncio.wait_for(event_ready.wait(), timeout=0.12)
            except TimeoutError:
                continue
            event_ready.clear()
            self.drain_events_once()
        result = await task
        self.drain_events_once()
        return result

    def _operation_elapsed(self) -> str:
        if self._operation_started_at is None:
            return "0s"
        return f"{int(time.monotonic() - self._operation_started_at)}s"

    def _elapsed(self) -> str:
        return f"{int(time.monotonic() - self._started_at)}s"

    def _activity_toolbar(self) -> str:
        if not self._activity_text:
            return ""
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        spinner = frames[self._activity_tick % len(frames)]
        operation = self._activity_event
        text = self._activity_text if operation is None else self._operation_text(operation)
        return f" {spinner} {text}  输入 /help 查看指令"

    def _invalidate_prompt(self) -> None:
        app = getattr(self._prompt, "app", None)
        if app is not None:
            app.invalidate()

    def _print(self, text: object, *, style: str | None = None) -> None:
        """Print above a live prompt without disturbing its input buffer."""
        def render() -> None:
            try:
                # Activity text contains literal prompt markers such as [review].
                # Keep them visible instead of letting Rich treat them as markup.
                self._console.print(text, style=style, highlight=False, markup=False)
            except TypeError:
                self._console.print(text, style=style)

        prompt_app = getattr(self._prompt, "app", None)
        if prompt_app is None:
            render()
            return
        try:
            from prompt_toolkit.application import run_in_terminal

            asyncio.get_running_loop()
        except RuntimeError:
            render()
            return
        run_in_terminal(render, in_executor=False)

    def _show_explanation(self) -> None:
        if self._last_event is None:
            message = "SafeFix 正在准备修复会话。"
        else:
            payload = self._last_event.safe_payload
            if payload.get("status") == "running":
                message = (
                    "SafeFix 仍在执行当前操作。"
                    "修复模型请求最多可能需要 120 秒。"
                )
            elif payload.get("status") == "error":
                message = f"最近一次操作失败：{payload.get('summary', '未知错误')}"
            elif self._last_event.kind == "model-call":
                message = "修复模型已响应；SafeFix 正在验证下一步操作。"
            else:
                message = str(payload.get("summary", "SafeFix 已准备就绪。"))
        self._print(f"SafeFix\n{message}", style="bright_white")

    def _show_help(self) -> None:
        self._print(
            "SafeFix 指令\n"
            "/explain  切换到只读说明模式\n"
            "/guide    切换到修复指导模式\n"
            "/tests    选择 existing、generated 或 mixed 测试\n"
            "/review   开启或关闭最终检查\n"
            "/start    完成准备后开始修复\n"
            "/status   查看当前状态\n"
            "/pause    在安全边界暂停\n"
            "/resume   继续修复\n"
            "/stop     请求安全停止\n"
            "/approve  通过待审批操作\n"
            "/deny     拒绝待审批操作\n"
            "/logs     控制原始日志（on|off|show）\n"
            "/help     显示此帮助",
            style="blue",
        )

    def _submit_input(self, line: str) -> None:
        normalized = line.strip()
        if not normalized:
            return
        if self._input_mode == "review":
            lowered = normalized.lower()
            if lowered in {"on", "off"}:
                normalized = f"/review {lowered}"
            elif lowered.startswith("review "):
                normalized = f"/{normalized}"
            elif not normalized.startswith("/"):
                self._print(
                    "● CONTROL  最终检查阶段请输入 on 或 off：on 表示修复成功后调用检查模型复核补丁，off 表示跳过最终检查。",
                    style="yellow",
                )
                return
        if self._input_mode == "tests" and not normalized.startswith("/"):
            normalized = f"/tests {normalized}"
        if normalized == "/explain":
            self._input_mode = "explain"
            self._print("● CONTROL  已切换到说明模式。", style="cyan")
            return
        if normalized == "/guide":
            self._input_mode = "guide"
            self._print("● CONTROL  已切换到修复指导模式。", style="cyan")
            return
        if normalized == "/help":
            self._show_help()
            return
        if normalized == "/start":
            if self._baseline_event is not None:
                self._print("请先输入 /tests existing、/tests generated 或 /tests mixed。", style="yellow")
            elif self._start_event is None or self._controller_active:
                self._print("修复已经在运行中。", style="yellow")
            elif not self._review_selected:
                self._print("请先在 [review] 阶段输入 on 或 off。", style="yellow")
            else:
                self._start_event.set()
                self._print("● CONTROL  已确认开始修复。", style="green")
            return
        if normalized.startswith("/tests "):
            choice = normalized.removeprefix("/tests ").strip().lower()
            choice = {"mix": "mixed"}.get(choice, choice)
            if choice not in {"existing", "generated", "mixed"}:
                self._print("用法：/tests existing、/tests generated 或 /tests mixed。", style="yellow")
            elif self._baseline_event is None:
                self._print("baseline 已冻结，不能再更改测试来源。", style="yellow")
            elif self._controller is None or not hasattr(self._controller, "configure_preflight"):
                self._print("当前无法选择测试来源。", style="red")
            else:
                try:
                    self._controller.configure_preflight(tests=choice)
                except RuntimeError as error:
                    self._print(f"● ERROR  {error}", style="red")
                else:
                    source_text = {"existing": "已有测试", "generated": "生成测试", "mixed": "混合测试"}[choice]
                    self._print(f"● CONTROL  已选择测试来源：{source_text}。", style="yellow")
                    if choice in {"generated", "mixed"}:
                        self._print("● TEST  正在请求 Test Model 生成测试", style="cyan")
                    else:
                        self._print("● TEST  正在准备已有测试 baseline", style="cyan")
                    self._baseline_event.set()
            return
        if normalized == "/review":
            if self._controller_active or self._controller is None or not hasattr(self._controller, "run_final_review_now"):
                self._print("只有成功完成修复后才能手动执行最终检查。", style="yellow")
            else:
                self._print("● REVIEW  正在执行手动最终检查。", style="yellow")
                try:
                    result = self._controller.run_final_review_now()
                except (OSError, RuntimeError) as error:
                    self._print(f"● ERROR  {error}", style="red")
                else:
                    verdict = {
                        "pass": "通过",
                        "warn": "警告",
                        "review_required": "需要人工复核",
                        "not_configured": "未配置",
                    }[result.verdict.value]
                    self._print(f"● REVIEW  最终检查{verdict}：{result.summary}", style="green")
            return
        if normalized.startswith("/review "):
            choice = normalized.removeprefix("/review ").strip().lower()
            if choice not in {"on", "off"}:
                self._print("用法：/review on 或 /review off。", style="yellow")
            elif self._baseline_event is not None:
                self._print("请先选择测试来源并建立 baseline。", style="yellow")
            elif self._controller is None or not hasattr(self._controller, "configure_preflight"):
                self._print("当前无法选择 Review。", style="red")
            else:
                try:
                    self._controller.configure_preflight(review=choice == "on")
                except RuntimeError as error:
                    self._print(f"● ERROR  {error}", style="red")
                else:
                    self._review_selected = True
                    self._input_mode = "explain"
                    self._invalidate_prompt()
                    review_text = "开启" if choice == "on" else "关闭"
                    self._print(
                        f"● CONTROL  最终检查已{review_text}。修复成功后将"
                        f"{'调用检查模型复核补丁' if choice == 'on' else '跳过最终检查'}。",
                        style="yellow",
                    )
                    if self._review_event is not None:
                        self._review_event.set()
            return
        if normalized == "/status":
            if self._controller is not None and hasattr(self._controller, "state") and not self._controller_active:
                self._show_status()
                return
            self.command_queue.submit_text(normalized)
            return
        if normalized == "/logs" or normalized.startswith("/logs "):
            self._handle_logs_command(normalized)
            return
        if normalized.startswith("/"):
            command = normalized[1:].lower()
            if command in {"pause", "resume", "stop", "approve", "deny"}:
                if self._controller is not None and hasattr(self._controller, "state") and not self._controller_active:
                    if command in {"approve", "deny"} and getattr(
                        self._controller, "pending_approval", False
                    ):
                        resolve = getattr(
                            self._controller,
                            "approve_pending" if command == "approve" else "deny_pending",
                        )
                        if callable(resolve) and resolve():
                            verdict = "已通过" if command == "approve" else "已拒绝"
                            self._print(f"● CONTROL  生成测试候选{verdict}。", style="yellow")
                        else:
                            self._print("● ERROR  未能处理待审批候选。", style="red")
                        return
                    self._print("本次运行已经结束，不能再执行该控制指令。", style="yellow")
                    return
                self.command_queue.submit_text(normalized)
            else:
                self._print(f"未知指令：{normalized} · 请输入 /help 查看帮助。", style="yellow")
            return
        if self._input_mode == "guide":
            if self._controller is not None and hasattr(self._controller, "state") and not self._controller_active:
                self._print("本次运行已经结束，不能再提交修复指导。", style="yellow")
                return
            self.command_queue.submit_text(normalized)
            self._print("● GUIDE  已排队，将在下一个安全边界应用。", style="yellow")
            return
        if self._controller_active:
            self.command_queue.submit_explanation(normalized)
            self._print(
                "● EXPLAIN  问题已排队，将在下一个安全边界回答。",
                style="bright_white",
            )
            return
        if self._controller is not None and hasattr(self._controller, "answer_explanation"):
            self._print("● EXPLAIN  正在回答你的问题。", style="cyan")
            try:
                self._controller.answer_explanation(normalized)
            except (OSError, RuntimeError):
                self._print("● ERROR  说明请求失败。", style="red")
            else:
                self.drain_events_once()
            return
        self._show_explanation()

    def _show_status(self) -> None:
        if self._controller is None or getattr(self._controller, "state", None) is None:
            self._show_explanation()
            return
        state = self._controller.state
        self._print(
            "SafeFix 状态\n"
            f"阶段      {self._controller.phase.value}\n"
            f"决策次数  {state.steps}\n"
            f"修复轮次  {state.rounds}\n"
            f"失败测试  当前 {len(state.F.ids)} · 最佳 {len(state.U_best.ids)}\n"
            f"项目路径  {self._controller.project_root}",
            style="green",
        )

    @staticmethod
    def _preflight_failure_text(reason: StopReason, detail: str | None = None) -> str:
        suffix = f"具体原因：{detail}" if detail else ""
        if reason is StopReason.TEST_PREPARATION_ERROR:
            return (
                "● ERROR  未能建立 baseline：Test Model 没有产生可接受的测试。"
                f"{suffix} 可执行 /logs on 查看模型原始响应，检查测试模型配置和返回格式后重新选择 /tests。"
            )
        if reason is StopReason.CONFIG_ERROR:
            return (
                "● ERROR  未能建立 baseline：项目配置、凭据或 pytest 测试发现失败。"
                f"{suffix} 请检查 safefix.toml、对应环境变量和 pytest 输出后重新选择 /tests。"
            )
        return f"● ERROR  未能建立 baseline。{suffix} 请检查 /logs 后重新选择 /tests。"

    def _handle_logs_command(self, command: str) -> None:
        argument = command.removeprefix("/logs").strip().lower()
        if argument in {"", "on"}:
            self._raw_logs_enabled = True
            self._print("● CONTROL  原始日志：on", style="yellow")
            return
        if argument == "off":
            self._raw_logs_enabled = False
            self._print("● CONTROL  原始日志：off", style="yellow")
            return
        if argument == "show":
            if self._raw_logs_truncated:
                self._print("较早的原始日志已截断。", style="yellow")
            if not self._raw_logs:
                self._print("当前没有收集到原始日志。", style="dim")
            for line in self._raw_logs:
                self._print(line, style="dim")
            return
        self._print("未知 /logs 选项：" + (argument or "（空）") + " · 请使用 /logs on、/logs off 或 /logs show。", style="yellow")

    def record_raw_log(self, text: str) -> None:
        """Retain bounded diagnostic text without making it a session artifact."""
        self._raw_logs.append(text)
        if len(self._raw_logs) > 100:
            self._raw_logs.pop(0)
            self._raw_logs_truncated = True
        if self._raw_logs_enabled:
            self._print(text, style="dim")

    def run(self) -> SessionResult:
        if self._capabilities.interactive:
            return asyncio.run(self._run_interactive())
        return self._run_without_input()

    def _run_without_input(self) -> SessionResult:
        controller = self._controller_factory(TuiEventSink(self._events), self.command_queue)
        self._controller = controller

        def run_controller() -> None:
            self._controller_active = True
            try:
                self._result = controller.run()
            except BaseException as error:
                self._failure = error
            finally:
                self._controller_active = False

        worker = threading.Thread(target=run_controller, name="safefix-runner")
        worker.start()
        worker.join()
        self.drain_events_once()
        return self._completed_result()

    async def _run_interactive(self) -> SessionResult:
        loop = asyncio.get_running_loop()
        event_ready = asyncio.Event()
        controller_finished = asyncio.Event()

        def notify_event() -> None:
            loop.call_soon_threadsafe(event_ready.set)

        controller = self._controller_factory(
            TuiEventSink(self._events, notify_event), self.command_queue
        )
        self._controller = controller
        self._print_start_report(controller)

        def run_controller() -> None:
            self._controller_active = True
            try:
                self._result = controller.run()
            except BaseException as error:
                self._failure = error
            finally:
                self._controller_active = False
                loop.call_soon_threadsafe(controller_finished.set)

        worker = threading.Thread(target=run_controller, name="safefix-runner")
        with patch_stdout(raw=True):
            input_task: asyncio.Task[None] | None = asyncio.create_task(self._read_input())
            activity_task = asyncio.create_task(self._animate_activity())
            event_task = asyncio.create_task(event_ready.wait())
            finished_task = asyncio.create_task(controller_finished.wait())
            try:
                self._start_event = asyncio.Event()
                self._baseline_event = asyncio.Event()
                self._review_event = asyncio.Event()
                prepare = getattr(controller, "prepare", None)
                project_root = getattr(controller, "project_root", None)
                if not callable(prepare) or project_root is None:
                    self._start_event.set()
                else:
                    self._print(
                        "● CONTROL  请输入 /tests existing、/tests generated 或 /tests mixed 来建立 baseline",
                        style="yellow",
                    )
                    while True:
                        await self._baseline_event.wait()
                        self._baseline_event = None
                        early_stop = await self._prepare_preflight(prepare, event_ready)
                        if early_stop is not None and not (
                            early_stop.stop_reason is StopReason.SUCCESS and controller.state is not None
                        ):
                            self._print(
                                self._preflight_failure_text(
                                    early_stop.stop_reason,
                                    getattr(controller, "preflight_failure_detail", None),
                                ),
                                style="red",
                            )
                            self._input_mode = "tests"
                            self._invalidate_prompt()
                            controller = self._controller_factory(
                                TuiEventSink(self._events, notify_event), self.command_queue
                            )
                            self._controller = controller
                            prepare = getattr(controller, "prepare")
                            self._baseline_event = asyncio.Event()
                            continue
                        assert controller.state is not None
                        summary = controller.state.preparation_summary
                        collected = (
                            summary.baseline_test_count
                            if summary is not None
                            else 0
                        )
                        self._print(
                            "● TEST  基线已冻结："
                            f"已收集 {collected} 个测试 · "
                            f"{len(controller.state.F0.ids)} 个失败测试等待修复。",
                            style="cyan",
                        )
                        if summary is not None and summary.baseline_source.value in {
                            "generated",
                            "mixed",
                        }:
                            self._print(
                                "● TEST  Test Model 生成统计："
                                f"候选 {summary.generated_candidate_count} 个 · "
                                f"已接受 {summary.generated_accepted_count} 个 · "
                                f"未接受 {summary.generated_candidate_count - summary.generated_accepted_count} 个。",
                                style="cyan",
                            )
                            self._print(
                                "● TEST  行为与关键分支覆盖（已执行校验）："
                                f"已覆盖 {len(summary.covered_requirement_ids)}/"
                                f"{len(summary.coverage_requirements)} 项。",
                                style="cyan",
                            )
                        self._input_mode = "review"
                        if input_task is not None:
                            input_task.cancel()
                            with suppress(asyncio.CancelledError):
                                await input_task
                            input_task = asyncio.create_task(self._read_input())
                        self._print(
                            "● CONTROL  请在 [review] 中输入 on 或 off：on 表示修复成功后调用检查模型复核补丁，off 表示跳过最终检查。",
                            style="yellow",
                        )
                        await self._review_event.wait()
                        self._review_event = None
                        self._input_mode = "explain"
                        if input_task is not None:
                            input_task.cancel()
                            with suppress(asyncio.CancelledError):
                                await input_task
                            input_task = asyncio.create_task(self._read_input())
                        self._print("● EXPLAIN  正在总结冻结 baseline", style="cyan")
                        controller.answer_explanation(
                            "Reply in Chinese. Summarize the frozen baseline. State every known failing test and likely bug area; if there are no failures, say that clearly. Confirm that no files have been modified."
                        )
                        self.drain_events_once()
                        self._print(
                            "● CONTROL  baseline 说明完成后请输入 /start 开始修复。",
                            style="yellow",
                        )
                        break
                await self._start_event.wait()
                self._start_event = None
                self._controller_active = True
                worker.start()
                try:
                    while not finished_task.done():
                        waiting: set[asyncio.Task[None]] = {event_task, finished_task}
                        if input_task is not None:
                            waiting.add(input_task)
                        done, _pending = await asyncio.wait(
                            waiting, return_when=asyncio.FIRST_COMPLETED
                        )
                        if event_task in done:
                            event_ready.clear()
                            self.drain_events_once()
                            event_task = asyncio.create_task(event_ready.wait())
                        if input_task is not None and input_task in done:
                            input_task = None
                except asyncio.CancelledError:
                    # SIGINT cancels the UI task, but the runner must finish
                    # its current atomic operation before stopping.
                    if self._controller_active:
                        self.command_queue.submit_text("/stop")
                        self._print("● CONTROL  已收到 Ctrl+C，已请求在安全边界停止。", style="yellow")
                    await asyncio.shield(controller_finished.wait())
                self.drain_events_once()
                self._print_final_report()
                if input_task is not None:
                    await input_task
            finally:
                self._activity_text = ""
                self._invalidate_prompt()
                activity_task.cancel()
                with suppress(asyncio.CancelledError):
                    await activity_task
                event_task.cancel()
                with suppress(asyncio.CancelledError):
                    await event_task
                if input_task is not None:
                    input_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await input_task

        worker.join()
        return self._completed_result()

    def _print_start_report(self, controller: SessionRunner) -> None:
        self._print(
            "SafeFix v0.2\n"
            f"项目      {getattr(getattr(controller, 'project_root', None), 'name', '当前项目')}\n"
            "模式      standard · 测试待选择 · 原始日志关闭",
            style="green",
        )

    def _print_final_report(self) -> None:
        if self._result is None:
            return
        state = getattr(self._controller, "state", None)
        baseline = len(state.F0.ids) if state is not None else 0
        best = len(state.U_best.ids) if state is not None else 0
        artifact = self._result.artifact_path or "未写入"
        review_line = ""
        if self._review_selected:
            if state is None or state.review_result is None:
                review_line = "\n最终检查  未执行（没有产生修复补丁）"
            else:
                verdict = {
                    "pass": "通过",
                    "warn": "警告",
                    "review_required": "需要人工复核",
                    "not_configured": "未配置",
                }[state.review_result.verdict.value]
                review_line = f"\n最终检查  {verdict}"
        self._print(
            "SafeFix 已完成\n"
            f"结果      {_localized_stop_reason(self._result.stop_reason.value)}\n"
            f"基线      {baseline} 个失败测试\n"
            f"最佳结果  {best} 个未解决失败\n"
            f"决策次数  {self._result.steps}\n"
            f"修复轮次  {self._result.rounds}\n"
            f"总用时    {self._elapsed()}\n"
            f"会话记录  {artifact}"
            f"{review_line}",
            style="green" if self._result.stop_reason.value == "success" else "yellow",
        )

    async def _animate_activity(self) -> None:
        while True:
            await asyncio.sleep(0.12)
            if self._activity_text:
                self._activity_tick += 1
                self._invalidate_prompt()

    def _completed_result(self) -> SessionResult:
        if self._failure is not None:
            raise self._failure
        return cast(SessionResult, self._result)

    async def _read_input(self, prompt: PromptSession | None = None) -> None:
        if prompt is None:
            try:
                prompt = self._input_factory(
                    bottom_toolbar=self._activity_toolbar,
                    completer=WordCompleter(
                        [
                            "/explain",
                            "/guide",
                            "/status",
                            "/pause",
                            "/resume",
                            "/stop",
                            "/approve",
                            "/deny",
                            "/start",
                            "/logs",
                            "/help",
                        ],
                        sentence=True,
                    ),
                )
            except TypeError:
                prompt = self._input_factory()
        self._prompt = prompt
        while True:
            try:
                try:
                    line = await prompt.prompt_async(
                        FormattedText([("bold ansicyan", self.prompt_text)])
                    )
                except TypeError:
                    line = await prompt.prompt_async(self.prompt_text)
            except (EOFError, KeyboardInterrupt, OSError):
                if self._controller_active:
                    self.command_queue.submit_text("/stop")
                return
            self._submit_input(line)
