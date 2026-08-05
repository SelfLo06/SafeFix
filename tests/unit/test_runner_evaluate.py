from pathlib import Path

import pytest

from safefix.junit import TestCaseResult as _TestCaseResult
from safefix.llm.mock import MockLLM
from safefix.models import StopReason
from safefix.testrunner import TestRunResult as _TestRunResult


class FakeCredentials:
    def get(self) -> str:
        return "test-api-key"


class SequentialTestRunner:
    def __init__(self, results: list[_TestRunResult]) -> None:
        self._results = iter(results)

    def run(self) -> _TestRunResult:
        return next(self._results)


def _report(exit_code: int, *failure_ids: str) -> _TestRunResult:
    cases = tuple(
        _TestCaseResult(failure_id, *failure_id.rsplit("::", 1), "failed")
        for failure_id in failure_ids
    )
    if not cases:
        cases = (_TestCaseResult("tests.app::test_passed", "tests.app", "test_passed", "passed"),)
    return _TestRunResult(exit_code=exit_code, cases=cases, valid=True)


def _invalid_report(exit_code: int) -> _TestRunResult:
    return _TestRunResult(exit_code=exit_code, cases=(), valid=False)


def _project(tmp_path: Path) -> None:
    (tmp_path / "safefix.toml").write_text(
        'base_url = "https://example.invalid"\nmodel = "test-model"\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")


def _runner(
    tmp_path: Path, reports: list[_TestRunResult], responses: list[str]
):
    from safefix.runner import SessionRunner

    test_runner = SequentialTestRunner(reports)
    return SessionRunner(
        tmp_path,
        credentials=FakeCredentials(),
        llm_client=MockLLM(responses),
        test_runner_factory=lambda project_root, pytest_args: test_runner,
    )


def _patch_then_finish() -> list[str]:
    return [
        '{"tool": "apply_patch", "changes": [{"path": "src/app.py", "old_text": "value = 1", "new_text": "value = 2"}]}',
        '{"tool": "finish", "reason": "evaluation complete"}',
    ]


def test_better_patch_updates_best(tmp_path: Path) -> None:
    _project(tmp_path)
    runner = _runner(
        tmp_path,
        [_report(1, "tests.app::test_a", "tests.app::test_b"), _report(1, "tests.app::test_a")],
        _patch_then_finish(),
    )

    result = runner.run()

    assert result.stop_reason is StopReason.REQUESTED
    assert result.rounds == 1
    assert result.no_progress == 0
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "value = 2\n"
    assert runner.state is not None
    assert runner.state.U_best.ids == frozenset({"tests.app::test_a"})
    assert runner.state.recent_tool_events[0][1].labels["failed"] == "1"
    assert runner.state.recent_tool_events[0][1].labels["error"] == "0"
    assert runner.snapshot_store is not None
    assert runner.snapshot_store.best_contents["src/app.py"] == "value = 2\n"


def test_same_patch_restores_best_and_increments_no_progress(tmp_path: Path) -> None:
    _project(tmp_path)
    runner = _runner(
        tmp_path,
        [_report(1, "tests.app::test_a"), _report(1, "tests.app::test_a")],
        _patch_then_finish(),
    )

    result = runner.run()

    assert result.stop_reason is StopReason.REQUESTED
    assert result.rounds == 1
    assert result.no_progress == 1
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    assert runner.state is not None
    assert runner.state.F == runner.state.U_best == runner.state.F0
    assert len(runner.state.patch_fingerprints) == 1


@pytest.mark.parametrize(
    "evaluated_failures",
    [
        ("tests.app::test_a", "tests.app::test_b", "tests.app::test_new"),
        ("tests.app::test_a", "tests.app::test_new"),
    ],
)
def test_worse_or_new_failure_restores_best(
    tmp_path: Path, evaluated_failures: tuple[str, ...]
) -> None:
    _project(tmp_path)
    runner = _runner(
        tmp_path,
        [
            _report(1, "tests.app::test_a", "tests.app::test_b"),
            _report(1, *evaluated_failures),
        ],
        _patch_then_finish(),
    )

    result = runner.run()

    assert result.stop_reason is StopReason.REQUESTED
    assert result.rounds == 1
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    assert runner.state is not None
    assert runner.state.F == runner.state.U_best == runner.state.F0


def test_successful_patch_counts_round(tmp_path: Path) -> None:
    _project(tmp_path)
    runner = _runner(
        tmp_path,
        [_report(1, "tests.app::test_a"), _report(0)],
        _patch_then_finish(),
    )

    result = runner.run()

    assert result.stop_reason is StopReason.SUCCESS
    assert result.rounds == 1
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "value = 2\n"


def test_post_patch_infra_error_restores_and_stops_error(tmp_path: Path) -> None:
    _project(tmp_path)
    runner = _runner(
        tmp_path,
        [_report(1, "tests.app::test_a"), _invalid_report(2)],
        _patch_then_finish(),
    )

    result = runner.run()

    assert result.stop_reason is StopReason.ERROR
    assert result.rounds == 0
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    assert runner.state is not None
    assert runner.state.F == runner.state.U_best == runner.state.F0

def test_guardrail_rejects_invalid_patch_without_runtime_error(tmp_path: Path) -> None:
    _project(tmp_path)
    runner = _runner(
        tmp_path,
        [_report(1, "tests.app::test_a")],
        [
            '{"tool": "apply_patch", "changes": [{"path": "src/app.py", '
            '"old_text": "not present", "new_text": "value = 2"}]}',
            '{"tool": "finish"}',
        ],
    )

    result = runner.run()

    assert result.stop_reason is StopReason.REQUESTED
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "value = 1\n"
