from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Sequence

from .junit import TestCaseResult, parse_junit_report


@dataclass(frozen=True)
class TestRunResult:
    exit_code: int
    cases: tuple[TestCaseResult, ...]
    stdout: str = ""
    stderr: str = ""
    valid: bool = False

    @property
    def failure_ids(self) -> frozenset[str]:
        return frozenset(case.failure_id for case in self.cases if case.is_failure)


class TestRunner:
    def __init__(
        self,
        project_root: str | Path,
        pytest_args: Sequence[str] = (),
        report_path: str | Path | None = None,
        target_paths: Sequence[str | Path] = (),
        allow_empty: bool = False,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.pytest_args = tuple(pytest_args)
        self.target_paths = tuple(str(path) for path in target_paths)
        self.allow_empty = allow_empty
        if report_path == "":
            raise ValueError("report_path must not be empty")
        if report_path is None:
            descriptor, temporary_report = tempfile.mkstemp(
                prefix="safefix-junit-", suffix=".xml"
            )
            os.close(descriptor)
            selected_report = Path(temporary_report)
            selected_report.unlink(missing_ok=True)
        else:
            selected_report = Path(report_path)
        self.report_path = (
            selected_report
            if selected_report.is_absolute()
            else self.project_root / selected_report
        )

    def run(self) -> TestRunResult:
        try:
            self.report_path.unlink(missing_ok=True)
        except OSError as exc:
            return TestRunResult(
                exit_code=3,
                cases=(),
                stderr=str(exc),
                valid=False,
            )
        command = [
            sys.executable,
            "-m",
            "pytest",
            *self.pytest_args,
            *self.target_paths,
            f"--junitxml={self.report_path}",
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=self.project_root,
                shell=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            return TestRunResult(
                exit_code=3,
                cases=(),
                stderr=str(exc),
                valid=False,
            )
        try:
            cases = parse_junit_report(self.report_path)
        except (OSError, ValueError) as exc:
            self.report_path.unlink(missing_ok=True)
            return TestRunResult(
                exit_code=completed.returncode,
                cases=(),
                stdout=completed.stdout,
                stderr=f"{completed.stderr}\n{exc}",
                valid=False,
            )
        try:
            self.report_path.unlink(missing_ok=True)
        except OSError as exc:
            return TestRunResult(
                exit_code=3,
                cases=(),
                stdout=completed.stdout,
                stderr=f"{completed.stderr}\n{exc}",
                valid=False,
            )
        has_collected_tests = any(not case.is_collection_error for case in cases)
        has_collection_error = any(case.is_collection_error for case in cases)
        return TestRunResult(
            exit_code=completed.returncode,
            cases=cases,
            stdout=completed.stdout,
            stderr=completed.stderr,
            valid=not has_collection_error
            and (has_collected_tests or self.allow_empty),
        )

    def collect_test_paths(self) -> tuple[str, ...]:
        """Return the project-relative files pytest actually collects."""
        command = [
            sys.executable,
            "-m",
            "pytest",
            *self.pytest_args,
            "--collect-only",
            "-q",
            *self.target_paths,
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=self.project_root,
                shell=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise OSError("pytest collection failed") from exc
        if completed.returncode != 0:
            raise ValueError("pytest collection failed")

        root = self.project_root.resolve()
        paths: set[str] = set()
        for line in completed.stdout.splitlines():
            node_id = line.strip()
            if "::" not in node_id:
                continue
            path_text = node_id.split("::", 1)[0]
            candidate = Path(path_text)
            resolved = (
                candidate if candidate.is_absolute() else root / candidate
            ).resolve()
            try:
                relative = resolved.relative_to(root)
            except ValueError:
                continue
            if resolved.is_file():
                paths.add(relative.as_posix())
        return tuple(sorted(paths))
