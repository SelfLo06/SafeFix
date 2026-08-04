from pathlib import Path

import pytest

from safefix.models import StopReason, ToolCall, ToolName
from safefix.tools.dispatch import dispatch


def test_dispatch_routes_read_file(tmp_path: Path):
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir()
    source.write_text("return 1\n", encoding="utf-8")

    result = dispatch(tmp_path, ToolCall(tool=ToolName.READ_FILE, path="src/app.py"))

    assert result == "return 1\n"


def test_dispatch_routes_list_dir(tmp_path: Path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "app.py").write_text("", encoding="utf-8")

    result = dispatch(tmp_path, ToolCall(tool=ToolName.LIST_DIR, path="src"))

    assert result == ["src/app.py"]


def test_dispatch_routes_search_code(tmp_path: Path):
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir()
    source.write_text("needle\n", encoding="utf-8")

    result = dispatch(
        tmp_path,
        ToolCall(tool=ToolName.SEARCH_CODE, path="src", query="needle"),
    )

    assert result == [("src/app.py", 1, "needle")]


@pytest.mark.parametrize("tool", [ToolName.LIST_DIR, ToolName.SEARCH_CODE])
def test_dispatch_requires_directory_tool_path(tmp_path: Path, tool: ToolName):
    action = ToolCall(tool=tool, query="needle" if tool is ToolName.SEARCH_CODE else None)

    with pytest.raises(ValueError, match="requires a path"):
        dispatch(tmp_path, action)


def test_dispatch_finish_requests_stop(tmp_path: Path):
    result = dispatch(
        tmp_path,
        ToolCall(tool=ToolName.FINISH, reason="done"),
    )

    assert result is StopReason.REQUESTED
