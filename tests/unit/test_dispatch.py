from pathlib import Path

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


def test_dispatch_finish_requests_stop(tmp_path: Path):
    result = dispatch(
        tmp_path,
        ToolCall(tool=ToolName.FINISH, reason="done"),
    )

    assert result is StopReason.REQUESTED
