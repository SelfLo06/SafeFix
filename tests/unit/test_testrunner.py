from pathlib import Path
import sys

import pytest

from safefix.testrunner import TestRunResult as _TestRunResult
from safefix.testrunner import TestRunner as Runner


def test_runner_executes_python_pytest_without_a_shell(tmp_path: Path, monkeypatch):
    calls = []
    report_paths = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        report = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--junitxml=")))
        report_paths.append(report)
        report.write_text(
            '<testsuites><testsuite name="pytest" tests="0" failures="0" errors="0" />'
            '</testsuites>'
        )
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("safefix.testrunner.subprocess.run", fake_run)

    runner = Runner(tmp_path)
    result = runner.run()

    assert calls[0][0][:3] == [sys.executable, "-m", "pytest"]
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["cwd"] == tmp_path
    assert result.exit_code == 0
    assert result.failure_ids == frozenset()
    assert result.valid is False
    assert not report_paths[0].exists()


def test_empty_result_is_invalid_by_default():
    result = _TestRunResult(exit_code=0, cases=())

    assert result.valid is False


def test_runner_marks_nonempty_junit_report_valid(tmp_path: Path, monkeypatch):
    report_paths = []

    def fake_run(command, **kwargs):
        report = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--junitxml=")))
        report_paths.append(report)
        report.write_text(
            '<testsuites><testsuite name="pytest" tests="1" failures="0" errors="0">'
            '<testcase classname="tests.test_app" name="test_ok" /></testsuite></testsuites>'
        )
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("safefix.testrunner.subprocess.run", fake_run)

    runner = Runner(tmp_path)
    result = runner.run()

    assert result.valid is True
    assert not report_paths[0].exists()


def test_runner_marks_collection_only_report_invalid(tmp_path: Path, monkeypatch):
    def fake_run(command, **kwargs):
        report = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--junitxml=")))
        report.write_text(
            '<testsuites><testsuite name="pytest"><error message="collection failed" />'
            '</testsuite></testsuites>'
        )
        return type("Completed", (), {"returncode": 1, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("safefix.testrunner.subprocess.run", fake_run)

    runner = Runner(tmp_path)
    result = runner.run()

    assert result.valid is False


def test_runner_removes_report_when_junit_parse_fails(tmp_path: Path, monkeypatch):
    report_paths = []

    def fake_run(command, **kwargs):
        report = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--junitxml=")))
        report_paths.append(report)
        report.write_text("not xml", encoding="utf-8")
        return type("Completed", (), {"returncode": 3, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("safefix.testrunner.subprocess.run", fake_run)

    runner = Runner(tmp_path)
    result = runner.run()

    assert result.valid is False
    assert report_paths
    assert not report_paths[0].exists()


def test_runner_resolves_relative_project_root_for_report_path(
    tmp_path: Path,
    monkeypatch,
):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(tmp_path)

    def fake_run(command, **kwargs):
        report_arg = next(
            arg.split("=", 1)[1]
            for arg in command
            if arg.startswith("--junitxml=")
        )
        report = Path(report_arg)
        if not report.is_absolute():
            report = Path(kwargs["cwd"]) / report
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            '<testsuites><testsuite name="pytest" tests="0" failures="0" errors="0" />'
            "</testsuites>"
        )
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("safefix.testrunner.subprocess.run", fake_run)

    result = Runner(Path("project")).run()

    assert result.exit_code == 0
    assert result.failure_ids == frozenset()


def test_runner_rejects_empty_report_path(tmp_path: Path):
    with pytest.raises(ValueError, match="report_path"):
        Runner(tmp_path, report_path="")
