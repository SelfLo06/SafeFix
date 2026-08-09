from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import tempfile
from typing import Callable

from ..config import MAX_STABILITY_RUNS
from ..models import CandidateStatus
from ..testrunner import TestRunResult
from .workspace import (
    CandidateWorkspace,
    _assert_no_symlink_components,
    _owned_workspace_for,
)


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
        candidate_root: str | Path,
    ) -> None:
        if isinstance(stability_runs, bool) or not isinstance(stability_runs, int):
            raise ValueError("stability_runs must be a positive integer")
        if not 1 <= stability_runs <= MAX_STABILITY_RUNS:
            raise ValueError(
                f"stability_runs must be between 1 and {MAX_STABILITY_RUNS}"
            )
        self._run_candidate = run_candidate
        self.stability_runs = stability_runs
        self._candidate_root = Path(candidate_root).absolute()
        self._candidate_workspace: CandidateWorkspace = self._validate_candidate_root()

    def evaluate(self, candidate: str | Path) -> CandidateEvaluation:
        self._validate_candidate_root()
        candidate_path = self._validate_candidate(candidate)
        original_contents = candidate_path.read_bytes()
        runs = tuple(
            self._run_isolated(candidate_path, original_contents, run_index)
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

        failure_ids = tuple(_failure_ids(result) for result in results)
        failure_signatures = tuple(_failure_signature(result) for result in results)
        if all(_is_green(result) for result in results):
            return CandidateEvaluation(
                candidate=candidate_path,
                status=CandidateStatus.PASS,
                runs=runs,
                stable_failure_ids=frozenset(),
                reason="all stability runs passed",
            )

        if (
            all(_is_red(result) for result in results)
            and len(set(failure_ids)) == 1
            and len(set(failure_signatures)) == 1
        ):
            return CandidateEvaluation(
                candidate=candidate_path,
                status=CandidateStatus.FAIL,
                runs=runs,
                stable_failure_ids=failure_ids[0],
                reason=(
                    "all stability runs failed with the same failure identities "
                    "and signatures"
                ),
            )

        return CandidateEvaluation(
            candidate=candidate_path,
            status=CandidateStatus.FLAKY,
            runs=runs,
            stable_failure_ids=frozenset(),
            reason=(
                "valid stability runs disagree in outcome, failure identity, "
                "or failure signature"
            ),
        )

    def _run_isolated(
        self, candidate: Path, original_contents: bytes, run_index: int
    ) -> CandidateRun:
        if candidate.is_symlink():
            raise ValueError("candidate path contains a symlink")
        if candidate.read_bytes() != original_contents:
            candidate.write_bytes(original_contents)
        run_root = Path(
            tempfile.mkdtemp(prefix=f".stability-{run_index}-", dir=self._candidate_root)
        )
        run_candidate = run_root / candidate.name
        run_candidate.write_bytes(original_contents)
        try:
            result = self._run_candidate(run_candidate)
        except Exception as exc:
            result = TestRunResult(
                exit_code=3,
                cases=(),
                stderr=f"{type(exc).__name__}: {exc}",
                valid=False,
            )
        finally:
            shutil.rmtree(run_root)
        return CandidateRun(candidate.stem, run_index, result)

    def _validate_candidate_root(self) -> CandidateWorkspace:
        if not self._candidate_root.is_dir():
            raise ValueError("candidate root must be an existing session directory")
        _assert_no_symlink_components(self._candidate_root, "candidate root")
        workspace = _owned_workspace_for(self._candidate_root)
        if workspace is None:
            raise ValueError("candidate root is not a live CandidateWorkspace session")
        if (
            hasattr(self, "_candidate_workspace")
            and workspace is not self._candidate_workspace
        ):
            raise ValueError(
                "candidate root is not the original CandidateWorkspace session"
            )
        return workspace

    def _validate_candidate(self, candidate: str | Path) -> Path:
        candidate_path = Path(candidate)
        if not candidate_path.is_absolute():
            candidate_path = self._candidate_root / candidate_path
        _assert_no_symlink_components(candidate_path, "candidate")
        try:
            resolved = candidate_path.resolve()
            resolved.relative_to(self._candidate_root.resolve())
        except ValueError as exc:
            raise ValueError("candidate path escapes the session-owned candidate root") from exc
        if not candidate_path.is_file():
            raise ValueError("candidate path must be an existing file")
        return candidate_path


def _is_valid(result: TestRunResult) -> bool:
    return result.valid and not any(
        case.is_collection_error for case in result.cases
    )


def _is_green(result: TestRunResult) -> bool:
    return result.exit_code == 0 and not result.failure_ids


def _is_red(result: TestRunResult) -> bool:
    return result.exit_code != 0 and bool(result.failure_ids)


def _failure_signature(result: TestRunResult) -> frozenset[tuple[str, str]]:
    return frozenset(
        (_stable_text(case.failure_id), _stable_text(case.message))
        for case in result.cases
        if case.is_failure
    )


def _failure_ids(result: TestRunResult) -> frozenset[str]:
    return frozenset(
        _stable_text(case.failure_id) for case in result.cases if case.is_failure
    )


def _stable_text(value: str) -> str:
    """Ignore the Harness-owned directory that differs between stability runs."""
    return re.sub(r"\.stability-\d+-[A-Za-z0-9_-]+", ".stability", value)
