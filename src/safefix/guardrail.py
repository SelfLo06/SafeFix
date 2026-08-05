from pathlib import Path
from typing import Any

from .models import Change, GuardDecision, ToolCall, ToolName
from .paths import (
    compute_writable_py_files,
    is_read_denied,
    is_read_path_obviously_denied,
    is_write_denied_path,
    normalize_rel_path,
)
from .patch_preflight import prepare_changes


class Guardrail:
    """Apply the pre-execution policy to parsed tool calls."""

    def __init__(
        self,
        project_root: Path,
        writable_paths: set[str | Path] | None = None,
        *,
        allowed_paths: list[str] | None = None,
        excluded_paths: list[str] | None = None,
    ) -> None:
        self._project_root = project_root.resolve()
        if writable_paths is None:
            self._writable_paths = compute_writable_py_files(
                self._project_root, allowed_paths, excluded_paths or []
            )
        else:
            self._writable_paths = {
                normalize_rel_path(self._project_root, str(path))
                for path in writable_paths
            }

    def check(self, action: Any) -> GuardDecision:
        if not isinstance(action, ToolCall):
            return GuardDecision.DENY
        if action.tool is not ToolName.APPLY_PATCH:
            if action.tool in {
                ToolName.READ_FILE,
                ToolName.LIST_DIR,
                ToolName.SEARCH_CODE,
            } and (
                action.path is None
                or is_read_path_obviously_denied(action.path)
                or is_read_denied(self._project_root, action.path)
            ):
                return GuardDecision.DENY
            return (
                GuardDecision.ALLOW
                if isinstance(action.tool, ToolName)
                else GuardDecision.DENY
            )
        if not action.changes:
            return GuardDecision.DENY

        paths: set[Path] = set()
        normalized_changes: list[Change] = []
        # Guardrail owns path policy and approval thresholds. The shared
        # preflight owns exact-match and overlap validation; apply_patch calls
        # it again at the write boundary to protect against TOCTOU changes.
        for change in action.changes:
            try:
                normalized = normalize_rel_path(self._project_root, change.path)
            except ValueError:
                return GuardDecision.DENY
            if is_write_denied_path(self._project_root, normalized):
                return GuardDecision.DENY
            if normalized not in self._writable_paths:
                return GuardDecision.DENY
            normalized_changes.append(
                Change(
                    normalized.relative_to(self._project_root).as_posix(),
                    change.old_text,
                    change.new_text,
                )
            )
            paths.add(normalized)

        try:
            prepare_changes(self._project_root, tuple(normalized_changes))
        except (OSError, ValueError):
            return GuardDecision.DENY

        if len(paths) > 3 or _changed_lines(action) > 80:
            return GuardDecision.REQUIRE_APPROVAL
        return GuardDecision.ALLOW

def _changed_lines(action: ToolCall) -> int:
    return sum(
        len(change.old_text.splitlines()) + len(change.new_text.splitlines())
        for change in action.changes
    )
