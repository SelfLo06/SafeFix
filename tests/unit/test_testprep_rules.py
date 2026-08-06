from pathlib import Path

import pytest

from safefix.testprep.models import GeneratedTestCandidate
from safefix.testprep.rules import validate_candidate


def make_candidate(source: str, *, sources=("src/app.py",), touched=()):
    return GeneratedTestCandidate(
        candidate_id="c1",
        test_source=source,
        basis="The public documentation describes this observable behavior.",
        sources=sources,
        touched_existing_tests=touched,
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "def parse(value):\n    return value\n", encoding="utf-8"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_existing.py").write_text(
        "def test_existing():\n    assert True\n", encoding="utf-8"
    )
    return tmp_path


def codes(candidate, project):
    return {violation.code for violation in validate_candidate(candidate, project)}


def test_accepts_simple_public_behavior_candidate(project):
    candidate = make_candidate(
        "import pytest\nfrom app import parse\n\ndef test_parse():\n    assert parse('x') == 'x'\n"
    )

    assert validate_candidate(candidate, project) == ()


def test_rejects_source_path_escape_and_existing_test_touch(project):
    candidate = make_candidate(
        "def test_parse():\n    assert True\n",
        sources=("../src/app.py",),
        touched=("tests/test_existing.py",),
    )

    violations = validate_candidate(candidate, project)

    assert {violation.code for violation in violations} >= {
        "source_path_escape",
        "existing_test_write",
    }


def test_validation_never_writes_production_or_existing_tests(project):
    production = project / "src" / "app.py"
    existing_test = project / "tests" / "test_existing.py"
    production_before = production.read_bytes()
    existing_before = existing_test.read_bytes()
    candidate = make_candidate(
        "from pathlib import Path\n\ndef test_write():\n    Path('src/app.py').write_text('bad')\n"
    )

    validate_candidate(candidate, project)

    assert production.read_bytes() == production_before
    assert existing_test.read_bytes() == existing_before


def test_rejects_excessive_patch_object_mocking(project):
    candidate = make_candidate(
        "from unittest.mock import patch\n\ndef test_mocking():\n"
        "    patch.object(object(), 'a')\n"
        "    patch.object(object(), 'b')\n"
        "    patch.object(object(), 'c')\n"
    )

    assert "excessive_mocking" in codes(candidate, project)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from app import Service\n\ndef test_private():\n    assert Service()._value == 1\n", "private_implementation"),
        ("import pytest\n\ndef test_error():\n    with pytest.raises(CustomGuess):\n        parse('x')\n", "unsupported_exception"),
        ("import requests\n\ndef test_network():\n    assert requests.get('https://example.test').status_code == 200\n", "nondeterministic_behavior"),
        ("import random\n\ndef test_random():\n    assert random.random() >= 0\n", "nondeterministic_behavior"),
        ("import time\n\ndef test_speed():\n    elapsed = time.perf_counter()\n    assert elapsed < 1\n", "performance_threshold"),
        ("from syrupy import Snapshot\n\ndef test_snapshot(snapshot):\n    assert parse('x') == snapshot\n", "complex_snapshot"),
        ("from unittest.mock import Mock\n\ndef test_mocking():\n    Mock(); Mock(); Mock()\n    assert True\n", "excessive_mocking"),
        ("import undeclared_package\n\ndef test_import():\n    assert True\n", "undeclared_import"),
        ("from pathlib import Path\n\ndef test_write():\n    Path('src/app.py').write_text('bad')\n", "non_test_source_edit"),
    ],
)
def test_rejects_forbidden_static_rule_families(project, source, expected):
    violation_codes = codes(make_candidate(source), project)

    assert expected in violation_codes


def test_rule_reasons_are_deterministic_and_ordered(project):
    candidate = make_candidate(
        "import undeclared_package\n\ndef test_bad():\n    assert object()._private == random.random()\n"
    )

    first = validate_candidate(candidate, project)
    second = validate_candidate(candidate, project)

    assert first == second
    assert tuple(violation.code for violation in first) == tuple(
        sorted(violation.code for violation in first)
    )
