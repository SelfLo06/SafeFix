import json
from pathlib import Path
import threading

import pytest

from safefix import review
from safefix.junit import TestCaseResult as _TestCaseResult
from safefix.llm.mock import MockLLM
from safefix.models import AcceptanceMode, BaselineSource, Config, ReviewVerdict, StopReason
from safefix.operator import OperatorCommandQueue
from safefix.approval import ApprovalProvider
from safefix.events import SessionEvent
from safefix.review import ReviewParseError, ReviewResult
from safefix.runner import SessionRunner
from safefix.session_setup import manifest_from_entries
from safefix.test_manifest import ManifestEntry
from safefix.testprep.service import PreparationResult, PreparationSummary
from safefix.testrunner import TestRunResult as _TestRunResult


class FakeCredentials:
    def get(self) -> str:
        return "test-api-key"


class FakeReviewClient:
    def __init__(self, result: ReviewResult | Exception) -> None:
        self._result = result
        self.prompts: list[str] = []

    def review(self, prompt: str) -> ReviewResult:
        self.prompts.append(prompt)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeApproval:
    def __init__(self, approved: bool) -> None:
        self.approved = approved
        self.requests: list[object] = []

    def approve(self, request: object) -> bool:
        self.requests.append(request)
        return self.approved


def _result(*failure_ids: str) -> _TestRunResult:
    cases = tuple(
        _TestCaseResult(failure_id, *failure_id.rsplit("::", 1), "failed")
        for failure_id in failure_ids
    )
    if not cases:
        cases = (
            _TestCaseResult(
                "tests.test_app::test_passed", "tests.test_app", "test_passed", "passed"
            ),
        )
    return _TestRunResult(exit_code=1 if failure_ids else 0, cases=cases, valid=True)


def _review(verdict: ReviewVerdict) -> ReviewResult:
    return ReviewResult(
        verdict=verdict,
        basis_supported=True,
        invented_behavior=False,
        implementation_coupling=False,
        risk="low",
        summary="The final candidate is grounded in the frozen test evidence.",
    )


def _runner(
    tmp_path: Path,
    *,
    mode: AcceptanceMode,
    reports: list[_TestRunResult],
    responses: list[str],
    review_client: FakeReviewClient,
    approval: FakeApproval | None = None,
    operator_queue: OperatorCommandQueue | None = None,
    event_sink: object | None = None,
) -> SessionRunner:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_app.py").write_text(
        "def test_placeholder():\n    assert True\n", encoding="utf-8"
    )
    config = Config(
        base_url="https://repair.example/v1",
        model="repair-model",
        review_base_url="https://review.example/v1",
        review_model="review-model",
        acceptance_mode=mode,
    )
    evaluations = iter(reports)

    class FrozenRunner:
        def __init__(self, target_paths: tuple[str, ...], allow_empty: bool) -> None:
            self.target_paths = target_paths
            self.allow_empty = allow_empty

        def run(self) -> _TestRunResult:
            if not self.target_paths:
                return _TestRunResult(exit_code=0, cases=(), valid=True)
            return next(evaluations)

        def collect_test_paths(self) -> tuple[str, ...]:
            return ("tests/test_app.py",)

    def runner_factory(
        _project_root: Path,
        _pytest_args: list[str],
        *,
        target_paths: tuple[str, ...],
        allow_empty: bool,
    ) -> FrozenRunner:
        return FrozenRunner(target_paths, allow_empty)

    def preparation_factory(_request: object) -> PreparationResult:
        return PreparationResult(
            manifest_entries=(
                ManifestEntry("tests/test_app.py", "prepared", BaselineSource.EXISTING),
            ),
            summary=PreparationSummary(baseline_source=BaselineSource.EXISTING),
        )

    return SessionRunner(
        tmp_path,
        credentials=FakeCredentials(),
        config_loader=lambda *_args, **_kwargs: config,
        test_runner_factory=runner_factory,
        llm_client=MockLLM(responses),
        preparation_factory=preparation_factory,
        manifest_factory=manifest_from_entries,
        final_review_client=review_client,
        approval=approval,
        operator_queue=operator_queue,
        event_sink=event_sink,
    )


def _patch(old: str, new: str) -> str:
    return json.dumps(
        {
            "tool": "apply_patch",
            "changes": [{"path": "src/app.py", "old_text": old, "new_text": new}],
        }
    )


def test_final_review_service_passes_only_safe_final_request_to_review_client() -> None:
    client = FakeReviewClient(_review(ReviewVerdict.PASS))
    request = review.FinalReviewRequest(
        baseline_summary="baseline failures: test_a",
        final_diff_summary="src/app.py changed",
        changed_files=("src/app.py",),
        patch_diffs=(("src/app.py", "--- src/app.py\n+++ src/app.py\n@@\n-value = 1\n+value = 2\n"),),
        constraints="frozen manifest only",
        pytest_summary="1 collected, 0 failed, 0 errors",
    )

    result = review.FinalReviewService().review(request, client)

    assert result.verdict is ReviewVerdict.PASS
    assert "Return exactly one JSON object" in client.prompts[0]
    assert '"changed_files": ["src/app.py"]' in client.prompts[0]
    assert '"patch_diffs"' in client.prompts[0]
    assert "-value = 1" in client.prompts[0]


def test_final_review_receives_the_accepted_patch_diff(tmp_path: Path) -> None:
    client = FakeReviewClient(_review(ReviewVerdict.PASS))
    runner = _runner(
        tmp_path,
        mode=AcceptanceMode.STANDARD,
        reports=[_result("tests.test_app::test_broken"), _result()],
        responses=[_patch("value = 1", "value = 2")],
        review_client=client,
    )

    assert runner.run().stop_reason is StopReason.SUCCESS
    assert "-value = 1" in client.prompts[0]
    assert "+value = 2" in client.prompts[0]


def test_standard_review_required_still_returns_success_with_artifact_warning(tmp_path: Path) -> None:
    client = FakeReviewClient(_review(ReviewVerdict.REVIEW_REQUIRED))
    runner = _runner(
        tmp_path,
        mode=AcceptanceMode.STANDARD,
        reports=[_result("tests.test_app::test_broken"), _result()],
        responses=[_patch("value = 1", "value = 2")],
        review_client=client,
    )

    result = runner.run()

    artifact = json.loads((tmp_path / "safefix-session.json").read_text(encoding="utf-8"))
    assert result.stop_reason is StopReason.SUCCESS
    assert artifact["review_verdict"] == "review_required"
    assert artifact["review"]["warning"] is True
    assert len(client.prompts) == 1


def test_high_risk_review_required_accepts_through_final_review_gate(tmp_path: Path) -> None:
    client = FakeReviewClient(_review(ReviewVerdict.REVIEW_REQUIRED))
    approval = FakeApproval(True)
    runner = _runner(
        tmp_path,
        mode=AcceptanceMode.HIGH_RISK,
        reports=[_result("tests.test_app::test_broken"), _result()],
        responses=[_patch("value = 1", "value = 2")],
        review_client=client,
        approval=approval,
    )

    result = runner.run()

    assert result.stop_reason is StopReason.SUCCESS
    assert len(approval.requests) == 1
    assert isinstance(approval.requests[0], review.FinalReviewRequest)


def test_high_risk_final_review_gate_accepts_queued_approve(tmp_path: Path) -> None:
    client = FakeReviewClient(_review(ReviewVerdict.REVIEW_REQUIRED))
    queue = OperatorCommandQueue()
    events = _RunnerEvents()

    def fail_prompt(_prompt: str) -> str:
        raise AssertionError("the runner worker must not read terminal input")

    runner = _runner(
        tmp_path,
        mode=AcceptanceMode.HIGH_RISK,
        reports=[_result("tests.test_app::test_broken"), _result()],
        responses=[_patch("value = 1", "value = 2")],
        review_client=client,
        approval=ApprovalProvider(interactive=True, input_fn=fail_prompt),
        operator_queue=queue,
        event_sink=events,
    )

    result_box, thread, done = _start_runner(runner, events)
    assert events.wait_for("approval", "pending", done, result_box)
    queue.submit_text("/approve")
    done.wait()
    thread.join()
    _raise_worker_error(result_box)
    assert result_box["result"].stop_reason is StopReason.SUCCESS  # type: ignore[union-attr]


def test_high_risk_final_review_gate_rejects_queued_deny(tmp_path: Path) -> None:
    client = FakeReviewClient(_review(ReviewVerdict.REVIEW_REQUIRED))
    queue = OperatorCommandQueue()
    events = _RunnerEvents()

    def fail_prompt(_prompt: str) -> str:
        raise AssertionError("the runner worker must not read terminal input")

    runner = _runner(
        tmp_path,
        mode=AcceptanceMode.HIGH_RISK,
        reports=[_result("tests.test_app::test_broken"), _result()],
        responses=[_patch("value = 1", "value = 2")],
        review_client=client,
        approval=ApprovalProvider(interactive=True, input_fn=fail_prompt),
        operator_queue=queue,
        event_sink=events,
    )

    result_box, thread, done = _start_runner(runner, events)
    assert events.wait_for("approval", "pending", done, result_box)
    queue.submit_text("/deny")
    done.wait()
    thread.join()
    _raise_worker_error(result_box)
    assert result_box["result"].stop_reason is StopReason.FINAL_REVIEW_REJECTED  # type: ignore[union-attr]
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "value = 1\n"


@pytest.mark.parametrize(
    ("ignored_command", "resolution_command", "expected_reason"),
    (
        ("/deny", "/approve", StopReason.SUCCESS),
        ("/approve", "/deny", StopReason.FINAL_REVIEW_REJECTED),
    ),
)
def test_high_risk_final_review_pause_preserves_queued_gate(
    tmp_path: Path,
    ignored_command: str,
    resolution_command: str,
    expected_reason: StopReason,
) -> None:
    client = FakeReviewClient(_review(ReviewVerdict.REVIEW_REQUIRED))
    queue = OperatorCommandQueue()
    events = _RunnerEvents()
    runner = _runner(
        tmp_path,
        mode=AcceptanceMode.HIGH_RISK,
        reports=[_result("tests.test_app::test_broken"), _result()],
        responses=[_patch("value = 1", "value = 2")],
        review_client=client,
        approval=ApprovalProvider(interactive=True, input_fn=_fail_prompt),
        operator_queue=queue,
        event_sink=events,
    )

    result_box, thread, done = _start_runner(runner, events)
    assert events.wait_for("approval", "pending", done, result_box)
    queue.submit_text("/pause")
    assert events.wait_for("pause", "accepted", done, result_box)
    queue.submit_text(ignored_command)
    assert events.wait_for(ignored_command[1:], "ignored", done, result_box)
    assert runner.phase.value == "paused"
    assert runner.pending_approval
    queue.submit_text("/resume")
    assert events.wait_for("resume", "accepted", done, result_box)
    assert runner.pending_approval
    queue.submit_text(resolution_command)
    done.wait()
    thread.join()
    _raise_worker_error(result_box)
    assert result_box["result"].stop_reason is expected_reason  # type: ignore[union-attr]


def _fail_prompt(_prompt: str) -> str:
    raise AssertionError("the runner worker must not read terminal input")


class _RunnerEvents:
    def __init__(self) -> None:
        self._events: list[SessionEvent] = []
        self._condition = threading.Condition()

    def emit(self, event: SessionEvent) -> None:
        with self._condition:
            self._events.append(event)
            self._condition.notify_all()

    def wake(self) -> None:
        with self._condition:
            self._condition.notify_all()

    def wait_for(
        self,
        command: str,
        status: str,
        done: threading.Event,
        result_box: dict[str, object],
    ) -> bool:
        with self._condition:
            while not any(
                event.safe_payload.get("command") == command
                and event.safe_payload.get("status") == status
                for event in self._events
            ):
                if done.is_set():
                    _raise_worker_error(result_box)
                    raise AssertionError(
                        f"runner finished without {command} status={status}"
                    )
                self._condition.wait()
            return True


def _start_runner(
    runner: SessionRunner,
    events: _RunnerEvents,
) -> tuple[dict[str, object], threading.Thread, threading.Event]:
    result_box: dict[str, object] = {}
    done = threading.Event()

    def run_worker() -> None:
        try:
            result_box["result"] = runner.run()
        except BaseException as error:
            result_box["error"] = error
        finally:
            done.set()
            events.wake()

    thread = threading.Thread(target=run_worker)
    thread.start()
    return result_box, thread, done


def _raise_worker_error(result_box: dict[str, object]) -> None:
    error = result_box.get("error")
    if error is not None:
        raise error


def test_high_risk_green_without_final_review_client_is_not_success(tmp_path: Path) -> None:
    runner = _runner(
        tmp_path,
        mode=AcceptanceMode.HIGH_RISK,
        reports=[_result("tests.test_app::test_broken"), _result()],
        responses=[_patch("value = 1", "value = 2")],
        review_client=FakeReviewClient(_review(ReviewVerdict.PASS)),
    )
    runner._final_review_client = None

    result = runner.run()

    assert result.stop_reason is StopReason.CONFIG_ERROR


def test_high_risk_without_final_review_client_stops_before_clean_baseline_success(
    tmp_path: Path,
) -> None:
    runner = _runner(
        tmp_path,
        mode=AcceptanceMode.HIGH_RISK,
        reports=[_result()],
        responses=[],
        review_client=FakeReviewClient(_review(ReviewVerdict.PASS)),
    )
    runner._final_review_client = None

    result = runner.run()

    assert result.stop_reason is StopReason.CONFIG_ERROR


def test_high_risk_review_rejection_restores_explicit_pre_final_best(tmp_path: Path) -> None:
    client = FakeReviewClient(_review(ReviewVerdict.REVIEW_REQUIRED))
    runner = _runner(
        tmp_path,
        mode=AcceptanceMode.HIGH_RISK,
        reports=[
            _result("tests.test_app::test_first", "tests.test_app::test_second"),
            _result("tests.test_app::test_second"),
            _result(),
        ],
        responses=[
            _patch("value = 1", "value = 2"),
            _patch("value = 2", "value = 3"),
        ],
        review_client=client,
        approval=FakeApproval(False),
    )

    result = runner.run()

    assert result.stop_reason is StopReason.FINAL_REVIEW_REJECTED
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "value = 2\n"
    assert runner.state is not None
    assert runner.state.F.ids == {"tests.test_app::test_second"}
    assert runner.state.U_best.ids == {"tests.test_app::test_second"}
    assert runner.state.last_evaluated is not None
    assert runner.state.last_evaluated.ids == {"tests.test_app::test_second"}

    artifact = json.loads((tmp_path / "safefix-session.json").read_text(encoding="utf-8"))
    assert artifact["failure_sets"]["current"] == ["tests.test_app::test_second"]
    assert artifact["failure_sets"]["unresolved_best"] == [
        "tests.test_app::test_second"
    ]
    assert artifact["unresolved_current"] == ["tests.test_app::test_second"]
    assert artifact["failure_diffs"]["resolved"] == ["tests.test_app::test_first"]


def test_review_model_failure_after_green_maps_to_error(tmp_path: Path) -> None:
    client = FakeReviewClient(ReviewParseError("malformed response"))
    runner = _runner(
        tmp_path,
        mode=AcceptanceMode.STANDARD,
        reports=[_result("tests.test_app::test_broken"), _result()],
        responses=[_patch("value = 1", "value = 2")],
        review_client=client,
    )

    result = runner.run()

    assert result.stop_reason is StopReason.ERROR
    assert len(client.prompts) == 1


def test_red_frozen_manifest_pytest_never_calls_final_review(tmp_path: Path) -> None:
    client = FakeReviewClient(_review(ReviewVerdict.PASS))
    runner = _runner(
        tmp_path,
        mode=AcceptanceMode.STANDARD,
        reports=[
            _result("tests.test_app::test_broken"),
            _result("tests.test_app::test_broken"),
        ],
        responses=[_patch("value = 1", "value = 2"), '{"tool": "finish"}'],
        review_client=client,
    )

    result = runner.run()

    assert result.stop_reason is StopReason.REQUESTED
    assert client.prompts == []
