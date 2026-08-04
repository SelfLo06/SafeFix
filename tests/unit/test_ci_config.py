from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).parents[2]


def test_github_workflow_runs_unit_tests():
    workflow = (PROJECT_ROOT / ".github/workflows/ci.yml").read_text()

    assert re.search(r"(?m)^\s*push\s*:", workflow)
    assert "actions/checkout@" in workflow
    assert "actions/setup-python@" in workflow
    assert re.search(r"(?m)^\s*python-version:\s*[\"']?3\.11[\"']?\s*$", workflow)
    assert re.search(r"(?m)^\s*-?\s*run:\s*pip install -e \.\s*$", workflow)
    assert "python -m pytest" in workflow


def test_gitlab_has_unit_test_job():
    pipeline = (PROJECT_ROOT / ".gitlab-ci.yml").read_text()

    assert re.search(r"(?m)^unit-test:\s*$", pipeline)
    assert re.search(r"(?m)^image:\s*python:3\.11\s*$", pipeline)
    assert re.search(r"(?m)^\s*-\s*pip install -e \.\s*$", pipeline)
    assert "python -m pytest" in pipeline
