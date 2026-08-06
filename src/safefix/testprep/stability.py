from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..models import CandidateStatus
from ..testrunner import TestRunResult


@dataclass(frozen=True)
class CandidateRun:
    candidate_id: str
    run_index: int
    result: TestRunResult


@dataclass(frozen=True)
class CandidateEvaluation:
    candidate: Path
    status: CandidateStatus
    runs: tuple[CandidateRun, ...]
    stable_failure_ids: frozenset[str]
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "runs", tuple(self.runs))
        object.__setattr__(
            self, "stable_failure_ids", frozenset(self.stable_failure_ids)
        )


class StabilityRunner:
    """Run one staged candidate a bounded, exact number of times."""

    def __init__(
        self,
        run_candidate: Callable[[Path], TestRunResult],
        stability_runs: int,
    ) -> None:
        if isinstance(stability_runs, bool) or not isinstance(stability_runs, int):
            raise ValueError("stability_runs must be a positive integer")
        if stability_runs < 1:
            raise ValueError("stability_runs must be a positive integer")
        self._run_candidate = run_candidate
        self.stability_runs = stability_runs

    def evaluate(self, candidate: str | Path) -> CandidateEvaluation:
        candidate_path = Path(candidate)
        runs = tuple(
            self._run(candidate_path, run_index)
            for run_index in range(self.stability_runs)
        )
        results = tuple(run.result for run in runs)

        if any(not _is_valid(result) for result in results):
            return CandidateEvaluation(
                candidate=candidate_path,
                status=CandidateStatus.ERROR,
                runs=runs,
                stable_failure_ids=frozenset(),
                reason="one or more stability runs had a collection or infrastructure error",
            )

        signatures = tuple(result.failure_ids for result in results)
        if all(_is_green(result) for result in results):
            return CandidateEvaluation(
                candidate=candidate_path,
                status=CandidateStatus.PASS,
                runs=runs,
                stable_failure_ids=frozenset(),
                reason="all stability runs passed",
            )

        if all(_is_red(result) for result in results) and len(set(signatures)) == 1:
            return CandidateEvaluation(
                candidate=candidate_path,
                status=CandidateStatus.FAIL,
                runs=runs,
                stable_failure_ids=signatures[0],
                reason="all stability runs failed with the same failure identities",
            )

        return CandidateEvaluation(
            candidate=candidate_path,
            status=CandidateStatus.FLAKY,
            runs=runs,
            stable_failure_ids=frozenset(),
            reason="valid stability runs disagree in outcome or failure identity",
        )

    def _run(self, candidate: Path, run_index: int) -> CandidateRun:
        try:
            result = self._run_candidate(candidate)
        except Exception as exc:
            result = TestRunResult(
                exit_code=3,
                cases=(),
                stderr=f"{type(exc).__name__}: {exc}",
                valid=False,
            )
        return CandidateRun(candidate.stem, run_index, result)


def _is_valid(result: TestRunResult) -> bool:
    return result.valid and not any(
        case.is_collection_error for case in result.cases
    )


def _is_green(result: TestRunResult) -> bool:
    return result.exit_code == 0 and not result.failure_ids


def _is_red(result: TestRunResult) -> bool:
    return result.exit_code != 0 and bool(result.failure_ids)
