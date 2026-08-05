from pathlib import Path

import pytest

from safefix.tools.finish import finish
from safefix.tools.read_file import read_file
from safefix.tools.search_code import search_code
from safefix.tools.list_dir import list_dir
from safefix.models import StopReason


def test_read_file_reads_readable_file(tmp_path: Path):
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir()
    source.write_text("return 1\n", encoding="utf-8")

    assert read_file(tmp_path, "src/app.py") == "return 1\n"


def test_read_file_denies_root_escape(tmp_path: Path):
    with pytest.raises(ValueError, match="project root"):
        read_file(tmp_path, "../outside.py")


def test_search_code_finds_string(tmp_path: Path):
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir()
    source.write_text("def run():\n    return 'needle'\n", encoding="utf-8")

    matches = search_code(tmp_path, ".", "needle")

    assert matches == [("src/app.py", 2, "    return 'needle'")]


def test_search_code_rejects_missing_path(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        search_code(tmp_path, "missing", "needle")


def test_search_code_requires_path_and_query(tmp_path: Path):
    with pytest.raises(ValueError, match="path and query"):
        search_code(tmp_path, "needle")


def test_list_dir_returns_bounded_listing(tmp_path: Path):
    for index in range(150):
        (tmp_path / f"file-{index}.py").write_text("", encoding="utf-8")

    assert len(list_dir(tmp_path)) <= 100


def test_search_code_returns_bounded_matches(tmp_path: Path):
    for index in range(150):
        (tmp_path / f"file-{index}.py").write_text("needle\n", encoding="utf-8")

    assert len(search_code(tmp_path, ".", "needle")) <= 100


def test_root_read_tools_filter_hard_denied_entries(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "secret.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "visible.py").write_text("needle\n", encoding="utf-8")

    assert ".git" not in list_dir(tmp_path)
    assert search_code(tmp_path, ".", "needle") == [("visible.py", 1, "needle")]


def test_search_code_order_is_deterministic(tmp_path: Path):
    for name in ("z.py", "a.py", "m.py"):
        (tmp_path / name).write_text("needle\n", encoding="utf-8")

    first = search_code(tmp_path, ".", "needle")
    second = search_code(tmp_path, ".", "needle")

    assert first == second == [
        ("a.py", 1, "needle"),
        ("m.py", 1, "needle"),
        ("z.py", 1, "needle"),
    ]


def test_finish_requests_stop():
    assert finish() is StopReason.REQUESTED
