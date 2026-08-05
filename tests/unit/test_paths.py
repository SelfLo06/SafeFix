from pathlib import Path

import pytest

from safefix.models import Config
from safefix.paths import (
    compute_writable_py_files,
    is_read_denied,
    is_write_denied,
    normalize_rel_path,
)


def test_tests_are_readable_but_not_writable(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    path = tmp_path / "tests" / "test_a.py"
    path.write_text("def test_a():\n    assert False\n", encoding="utf-8")

    assert is_read_denied(tmp_path, "tests/test_a.py") is False
    assert is_write_denied(tmp_path, "tests/test_a.py") is True


@pytest.mark.parametrize("relative", ["../outside.py", "src/../../outside.py", "/tmp/outside.py"])
def test_project_root_escape_is_denied(tmp_path: Path, relative: str):
    assert is_read_denied(tmp_path, relative) is True
    assert is_write_denied(tmp_path, relative) is True


@pytest.mark.parametrize(
    "relative",
    [
        ".git/config",
        ".venv/bin/python",
        "venv/lib/site.py",
        "src/__pycache__/x.pyc",
        ".pytest_cache/state",
        ".env",
        "credential",
        "credential.json",
        "credentials.json",
        "private.pem",
        "src/secret.py",
        "secrets/token.txt",
        "credentials/api.txt",
        "src/secret/token.py",
        "cache/data.py",
        ".cache/data.py",
    ],
)
def test_hard_excluded_paths_are_not_readable_or_writable(tmp_path: Path, relative: str):
    assert is_read_denied(tmp_path, relative) is True
    assert is_write_denied(tmp_path, relative) is True


def test_normalize_returns_resolved_path_inside_root(tmp_path: Path):
    assert normalize_rel_path(tmp_path, "src/../app.py") == (tmp_path / "app.py").resolve()


def test_src_default_writable(tmp_path: Path):
    (tmp_path / "src").mkdir()
    path = tmp_path / "src" / "module.py"
    path.write_text("value = 1\n", encoding="utf-8")

    assert compute_writable_py_files(tmp_path, None, []) == {path.resolve()}


def test_config_default_allowed_paths_make_src_writable(tmp_path: Path):
    (tmp_path / "src").mkdir()
    path = tmp_path / "src" / "module.py"
    path.write_text("value = 1\n", encoding="utf-8")

    assert compute_writable_py_files(tmp_path, Config().allowed_paths, []) == {path.resolve()}


def test_missing_src_defaults_to_project_python_sources(tmp_path: Path):
    path = tmp_path / "app.py"
    path.write_text("value = 1\n", encoding="utf-8")

    assert compute_writable_py_files(tmp_path, None, []) == {path.resolve()}


def test_explicit_allowed_paths_replace_default_derivation(tmp_path: Path):
    (tmp_path / "src").mkdir()
    src_path = tmp_path / "src" / "module.py"
    src_path.write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "lib").mkdir()
    lib_path = tmp_path / "lib" / "module.py"
    lib_path.write_text("value = 2\n", encoding="utf-8")

    assert compute_writable_py_files(tmp_path, ["lib"], []) == {lib_path.resolve()}


def test_excluded_paths_are_additive_to_hard_denies(tmp_path: Path):
    (tmp_path / "src").mkdir()
    keep = tmp_path / "src" / "keep.py"
    excluded = tmp_path / "src" / "generated.py"
    keep.write_text("value = 1\n", encoding="utf-8")
    excluded.write_text("value = 2\n", encoding="utf-8")

    assert compute_writable_py_files(tmp_path, None, ["src/generated.py"]) == {keep.resolve()}


def test_readable_source_is_not_writable_when_not_in_writable_set(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    test_path = tmp_path / "tests" / "helper.py"
    test_path.write_text("value = 1\n", encoding="utf-8")

    assert is_read_denied(tmp_path, "tests/helper.py") is False
    assert test_path.resolve() not in compute_writable_py_files(tmp_path, None, [])


def test_explicit_path_escape_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError):
        compute_writable_py_files(tmp_path, ["../outside"], [])
