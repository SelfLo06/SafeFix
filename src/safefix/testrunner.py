from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Sequence

from .junit import TestCaseResult, parse_junit_report


@dataclass(frozen=True)
class TestRunResult:
    exit_code: int
    cases: tuple[TestCaseResult, ...]
    stdout: str = ""
    stderr: str = ""

    @property
    def failure_ids(self) -> frozenset[str]:
        return frozenset(case.failure_id for case in self.cases if case.is_failure)


class TestRunner:
    def __init__(
        self,
        project_root: str | Path,
        pytest_args: Sequence[str] = (),
        report_path: str | Path | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.pytest_args = tuple(pytest_args)
        selected_report = report_path or Path(".safefix-junit.xml")
        selected_report = Path(selected_report)
        self.report_path = (
            selected_report
            if selected_report.is_absolute()
            else self.project_root / selected_report
        )

    def run(self) -> TestRunResult:
        command = [
            "python",
            "-m",
            "pytest",
            *self.pytest_args,
            f"--junitxml={self.report_path}",
        ]
        completed = subprocess.run(
            command,
            cwd=self.project_root,
            shell=False,
            capture_output=True,
            text=True,
        )
        cases = parse_junit_report(self.report_path)
        return TestRunResult(
            exit_code=completed.returncode,
            cases=cases,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
