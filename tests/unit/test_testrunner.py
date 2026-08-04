from pathlib import Path

from safefix.testrunner import TestRunner as Runner


def test_runner_executes_python_pytest_without_a_shell(tmp_path: Path, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        report = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--junitxml=")))
        report.write_text(
            '<testsuites><testsuite name="pytest" tests="0" failures="0" errors="0" />'
            '</testsuites>'
        )
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("safefix.testrunner.subprocess.run", fake_run)

    result = Runner(tmp_path).run()

    assert calls[0][0][:3] == ["python", "-m", "pytest"]
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["cwd"] == tmp_path
    assert result.exit_code == 0
    assert result.failure_ids == frozenset()
