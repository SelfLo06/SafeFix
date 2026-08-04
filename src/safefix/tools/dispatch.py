from pathlib import Path

from ..models import StopReason, ToolCall, ToolName
from ..snapshot import SnapshotStore
from .apply_patch import apply_patch
from .finish import finish
from .list_dir import list_dir
from .read_file import read_file
from .search_code import search_code


def dispatch(
    project_root: Path,
    action: ToolCall,
    snapshot_store: SnapshotStore | None = None,
) -> str | list[str] | list[tuple[str, int, str]] | StopReason | None:
    """Execute one validated ToolCall through its corresponding tool."""
    if not isinstance(action, ToolCall):
        raise TypeError("action must be a ToolCall")

    if action.tool is ToolName.READ_FILE:
        if action.path is None:
            raise ValueError("read_file requires a path")
        return read_file(project_root, action.path)
    if action.tool is ToolName.LIST_DIR:
        if action.path is None:
            raise ValueError("list_dir requires a path")
        return list_dir(project_root, action.path)
    if action.tool is ToolName.SEARCH_CODE:
        if action.path is None:
            raise ValueError("search_code requires a path")
        if action.query is None:
            raise ValueError("search_code requires a query")
        return search_code(project_root, action.path, action.query)
    if action.tool is ToolName.APPLY_PATCH:
        apply_patch(project_root, action.changes, snapshot_store)
        return None
    if action.tool is ToolName.FINISH:
        return finish(action.reason)
    raise ValueError(f"unsupported tool: {action.tool}")
