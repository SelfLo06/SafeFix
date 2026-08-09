from __future__ import annotations

from dataclasses import dataclass
import json
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
    executed_lines: dict[str, frozenset[int]] | None = None

    @property
    def failure_ids(self) -> frozenset[str]:
        return frozenset(case.failure_id for case in self.cases if case.is_failure)


_TRACE_RESULT_PREFIX = "SAFEFIX_TRACE_LINES="
_TRACE_PYTEST_SCRIPT = """
import json
import pathlib
import sys
import pytest

arguments = json.loads(sys.argv[1])
tracked = {str(pathlib.Path(path).resolve()) for path in json.loads(sys.argv[2])}
hits = {}
def trace(frame, event, argument):
    if event == \"line\":
        filename = str(pathlib.Path(frame.f_code.co_filename).resolve())
        if filename in tracked:
            hits.setdefault(filename, set()).add(frame.f_lineno)
    return trace
sys.settrace(trace)
try:
    exit_code = pytest.main(arguments)
finally:
    sys.settrace(None)
    print(\"SAFEFIX_TRACE_LINES=\" + json.dumps({key: sorted(value) for key, value in hits.items()}))
raise SystemExit(exit_code)
"""


class TestRunner:
    def __init__(
        self,
        project_root: str | Path,
        pytest_args: Sequence[str] = (),
        report_path: str | Path | None = None,
        target_paths: Sequence[str | Path] = (),
        allow_empty: bool = False,
        trace_paths: Sequence[str | Path] = (),
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.pytest_args = tuple(pytest_args)
        self.target_paths = tuple(str(path) for path in target_paths)
        self.allow_empty = allow_empty
        self.trace_paths = tuple(str(path) for path in trace_paths)
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
        pytest_command = [
            *self.pytest_args,
            *self.target_paths,
            f"--junitxml={self.report_path}",
        ]
        command = (
            [
                sys.executable,
                "-c",
                _TRACE_PYTEST_SCRIPT,
                json.dumps(pytest_command),
                json.dumps(
                    [str((self.project_root / path).resolve()) for path in self.trace_paths]
                ),
            ]
            if self.trace_paths
            else [sys.executable, "-m", "pytest", *pytest_command]
        )
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
        executed_lines = _trace_lines(completed.stdout, self.project_root)
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
            executed_lines=executed_lines,
        )

    def collect_test_paths(self) -> tuple[str, ...]:
        """Return the project-relative files pytest actually collects."""
        collection_args = [
            argument for argument in self.pytest_args if argument not in {"-q", "-v"}
        ]
        command = [
            sys.executable,
            "-m",
            "pytest",
            *collection_args,
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


def _trace_lines(stdout: str, project_root: Path) -> dict[str, frozenset[int]] | None:
    for line in reversed(stdout.splitlines()):
        if not line.startswith(_TRACE_RESULT_PREFIX):
            continue
        try:
            payload = json.loads(line.removeprefix(_TRACE_RESULT_PREFIX))
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        result: dict[str, frozenset[int]] = {}
        for raw_path, raw_lines in payload.items():
            if not isinstance(raw_path, str) or not isinstance(raw_lines, list):
                return None
            path = Path(raw_path)
            try:
                relative = path.resolve().relative_to(project_root.resolve()).as_posix()
            except ValueError:
                continue
            if not all(type(item) is int and item > 0 for item in raw_lines):
                return None
            result[relative] = frozenset(raw_lines)
        return result
    return None
