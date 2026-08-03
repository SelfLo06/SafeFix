from pathlib import Path

import pytest

from safefix.tools.finish import finish
from safefix.tools.read_file import read_file
from safefix.tools.search_code import search_code
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


def test_finish_requests_stop():
    assert finish("repair complete") is StopReason.REQUESTED
