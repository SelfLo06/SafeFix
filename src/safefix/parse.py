import json
from pathlib import Path
from pathlib import PureWindowsPath
import posixpath
from typing import Any

from .models import Change, ToolCall, ToolName
from .paths import normalize_rel_path


class ParseError(ValueError):
    """Raised when an LLM response is not one valid ToolCall JSON object."""


class ActionParser:
    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root

    def parse(self, response: str) -> ToolCall:
        try:
            action = json.loads(response)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ParseError("response must be valid JSON") from exc

        if not isinstance(action, dict):
            raise ParseError("response must contain exactly one tool action object")
        if not isinstance(action.get("tool"), str):
            raise ParseError("action tool must be a string")

        tool_name = action["tool"]
        try:
            tool = ToolName(tool_name)
        except ValueError as exc:
            raise ParseError(f"unknown tool: {tool_name}") from exc

        if tool is ToolName.READ_FILE:
            self._require_fields(action, {"tool", "path"})
            return ToolCall(tool=tool, path=self._path(action["path"]))
        if tool is ToolName.LIST_DIR:
            self._require_fields(action, {"tool", "path"})
            return ToolCall(tool=tool, path=self._path(action["path"]))
        if tool is ToolName.SEARCH_CODE:
            self._require_fields(action, {"tool", "path", "query"})
            if not isinstance(action["query"], str):
                raise ParseError("query must be a string")
            return ToolCall(
                tool=tool,
                path=self._path(action["path"]),
                query=action["query"],
            )
        if tool is ToolName.APPLY_PATCH:
            self._require_fields(action, {"tool", "changes"})
            changes = action["changes"]
            if not isinstance(changes, list) or not changes:
                raise ParseError("changes must be a non-empty array")
            parsed_changes = []
            for change in changes:
                if not isinstance(change, dict) or set(change) != {
                    "path",
                    "old_text",
                    "new_text",
                }:
                    raise ParseError("each change must contain only path, old_text, and new_text")
                if not all(isinstance(change[field], str) for field in ("path", "old_text", "new_text")):
                    raise ParseError("change fields must be strings")
                normalized_path = self._path(change["path"])
                parsed_changes.append(
                    Change(normalized_path, change["old_text"], change["new_text"])
                )
            return ToolCall(tool=tool, changes=tuple(parsed_changes))

        if set(action) - {"tool", "reason"}:
            raise ParseError("action contains missing or unknown fields")
        reason = action.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise ParseError("reason must be a string")
        return ToolCall(tool=ToolName.FINISH, reason=reason)

    def _path(self, value: Any) -> str:
        if not isinstance(value, str) or not value:
            raise ParseError("path must be a non-empty string")
        if Path(value).is_absolute() or PureWindowsPath(value).is_absolute():
            raise ParseError("path must be project-relative")
        portable_value = value.replace("\\", "/")
        try:
            normalized = normalize_rel_path(self._project_root, portable_value)
        except ValueError as exc:
            if "escapes project root" in str(exc):
                return posixpath.normpath(portable_value)
            raise ParseError("path must be project-relative") from exc
        return normalized.relative_to(self._project_root.resolve()).as_posix()

    @staticmethod
    def _require_fields(action: dict[str, Any], expected: set[str]) -> None:
        if set(action) != expected:
            raise ParseError("action contains missing or unknown fields")
