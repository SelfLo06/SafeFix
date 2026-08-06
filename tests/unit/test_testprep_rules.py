from pathlib import Path

import pytest

from safefix.testprep.models import GeneratedTestCandidate, RuleViolation
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


@pytest.mark.parametrize(
    "source",
    [
        "from pathlib import Path\n\ndef test_write():\n    Path('src/app.py').open('w')\n",
        "from pathlib import Path as P\n\ndef test_write():\n    P('src/app.py').open(mode='wb')\n",
        "from pathlib import Path\n\ndef test_write():\n    Path('src/app.py').touch()\n",
        "import os as operating_system\n\ndef test_write():\n    operating_system.system('touch src/app.py')\n",
        "from os import system as execute\n\ndef test_write():\n    execute('touch src/app.py')\n",
        "import shutil as file_ops\n\ndef test_write():\n    file_ops.copyfile('src/app.py', 'tests/test_existing.py')\n",
        "from shutil import copyfile as copy_file\n\ndef test_write():\n    copy_file('src/app.py', 'tests/test_existing.py')\n",
        "import os\n\ndef test_write():\n    getattr(os, 'system')('touch src/app.py')\n",
        "import os\n\ndef test_write():\n    operation = getattr(os, 'system')\n    operation('touch src/app.py')\n",
        "import importlib\n\ndef test_write():\n    operating_system = importlib.import_module('os')\n    operating_system.system('touch src/app.py')\n",
        "import importlib\n\ndef test_write():\n    importlib.import_module('shutil').copyfile('src/app.py', 'tests/test_existing.py')\n",
    ],
)
def test_rejects_aliased_and_indirect_filesystem_writes(project, source):
    violations = validate_candidate(make_candidate(source), project)

    assert violations == (
        RuleViolation(
            "non_test_source_edit",
            "candidate must not write production or existing test files",
        ),
    )


def test_rejects_aliased_os_randomness(project):
    source = "from os import urandom as random_bytes\n\ndef test_random():\n    assert random_bytes(1)\n"

    assert validate_candidate(make_candidate(source), project) == (
        RuleViolation(
            "nondeterministic_behavior",
            "candidate must not depend on network, time, or randomness",
        ),
    )


def test_rejects_os_randomness_through_module_alias(project):
    source = "import os as operating_system\n\ndef test_random():\n    assert operating_system.urandom(1)\n"

    assert validate_candidate(make_candidate(source), project) == (
        RuleViolation(
            "nondeterministic_behavior",
            "candidate must not depend on network, time, or randomness",
        ),
    )


def test_allows_read_only_path_open(project):
    source = "from pathlib import Path\n\ndef test_read():\n    Path('src/app.py').open('r')\n"

    assert validate_candidate(make_candidate(source), project) == ()


def test_rejects_benchmark_fixture_call(project):
    source = "def test_speed(benchmark):\n    benchmark(lambda: 1)\n"

    assert validate_candidate(make_candidate(source), project) == (
        RuleViolation(
            "performance_threshold",
            "candidate must not assert a performance threshold",
        ),
    )


def test_rejects_aliased_unittest_mock_calls(project):
    source = "from unittest.mock import Mock as M\n\ndef test_mocking():\n    M(); M(); M()\n"

    assert validate_candidate(make_candidate(source), project) == (
        RuleViolation(
            "excessive_mocking",
            "candidate uses more than two mock operations",
        ),
    )


def test_rejects_getattr_private_access(project):
    source = "def test_private():\n    assert getattr(object(), '_private') == 1\n"

    assert validate_candidate(make_candidate(source), project) == (
        RuleViolation(
            "private_implementation",
            "candidate must assert public behavior, not private details",
        ),
    )


@pytest.mark.parametrize("source", [
    "def test_speed():\n    assert 10 > runtime\n",
    "def test_speed():\n    assert 1 < 2 < elapsed\n",
])
def test_rejects_performance_variables_on_either_side(project, source):
    assert validate_candidate(make_candidate(source), project) == (
        RuleViolation(
            "performance_threshold",
            "candidate must not assert a performance threshold",
        ),
    )


@pytest.mark.parametrize(
    "source",
    [
        "def test_replace():\n    assert 'a'.replace('a', 'b') == 'b'\n",
        "def test_remove():\n    values = [1, 2]\n    values.remove(1)\n    assert values == [2]\n",
    ],
)
def test_allows_in_memory_replace_and_remove(project, source):
    assert validate_candidate(make_candidate(source), project) == ()


@pytest.mark.parametrize(
    "source",
    [
        "from pathlib import Path\n\ndef test_replace():\n    Path('src/app.py').replace('src/other.py')\n",
        "import os as operating_system\n\ndef test_remove():\n    operating_system.remove('src/app.py')\n",
    ],
)
def test_still_rejects_filesystem_mutation_methods(project, source):
    assert validate_candidate(make_candidate(source), project) == (
        RuleViolation(
            "non_test_source_edit",
            "candidate must not write production or existing test files",
        ),
    )


@pytest.mark.parametrize(
    "source",
    [
        "fopen = open\n\ndef test_write():\n    fopen('src/app.py', 'w')\n",
        "import pathlib\n\nP = pathlib.Path\n\ndef test_write():\n    P.open(P('src/app.py'), 'w')\n",
        "import pathlib\n\nP = pathlib.Path\n\ndef test_write():\n    P.touch(P('src/app.py'))\n",
        "from pathlib import Path\n\np = Path('src/app.py')\nop = p.open\n\ndef test_write():\n    op('w')\n",
        "from pathlib import Path\n\np = Path('src/app.py')\ntouch = p.touch\n\ndef test_write():\n    touch()\n",
        "from pathlib import Path\n\ntouch = Path.touch\n\ndef test_write():\n    touch(Path('src/app.py'))\n",
        "from pathlib import Path\n\nop = Path.open\n\ndef test_write():\n    op(Path('src/app.py'), 'w')\n",
        "from builtins import open as fopen\n\ndef test_write():\n    fopen('src/app.py', 'w')\n",
        "import os\n\ng = getattr\n\ndef test_write():\n    g(os, 'system')('touch src/app.py')\n",
        "import importlib\n\ng = getattr\n\ndef test_write():\n    g(importlib.import_module('shutil'), 'copyfile')('src/app.py', 'tests/test_existing.py')\n",
    ],
)
def test_rejects_callable_aliases_that_can_write_files(project, source):
    assert validate_candidate(make_candidate(source), project) == (
        RuleViolation(
            "non_test_source_edit",
            "candidate must not write production or existing test files",
        ),
    )


@pytest.mark.parametrize(
    "source",
    [
        "fopen = open\n\ndef test_read():\n    fopen('src/app.py', 'r')\n",
        "import pathlib\n\nP = pathlib.Path\n\ndef test_read():\n    P.open(P('src/app.py'), 'r')\n",
        "from pathlib import Path\n\np = Path('src/app.py')\nop = p.open\n\ndef test_read():\n    op('r')\n",
    ],
)
def test_allows_read_only_callable_aliases(project, source):
    assert validate_candidate(make_candidate(source), project) == ()


@pytest.mark.parametrize(
    ("source", "violation"),
    [
        (
            "import os\n\ng = getattr\n\ndef test_random():\n    assert g(os, 'urandom')(1)\n",
            RuleViolation(
                "nondeterministic_behavior",
                "candidate must not depend on network, time, or randomness",
            ),
        ),
        (
            "g = getattr\n\ndef test_private():\n    assert g(object(), '_private') == 1\n",
            RuleViolation(
                "private_implementation",
                "candidate must assert public behavior, not private details",
            ),
        ),
        (
            "import unittest.mock as um\n\ng = getattr\n\ndef test_mocking():\n    g(um, 'Mock')(); g(um, 'Mock')(); g(um, 'Mock')()\n",
            RuleViolation(
                "excessive_mocking",
                "candidate uses more than two mock operations",
            ),
        ),
        (
            "runtime = 1\n\nr = runtime\n\ndef test_speed():\n    assert 10 > r\n",
            RuleViolation(
                "performance_threshold",
                "candidate must not assert a performance threshold",
            ),
        ),
    ],
)
def test_rejects_getattr_and_performance_aliases(project, source, violation):
    assert validate_candidate(make_candidate(source), project) == (violation,)
