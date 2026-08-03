from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Callable

from ..models import Change
from ..paths import normalize_rel_path
from ..snapshot import SnapshotStore


Replace = Callable[[Path, Path], None]


def apply_patch(
    project_root: Path,
    changes: Iterable[Change],
    snapshot_store: SnapshotStore | None = None,
    *,
    replace: Replace | None = None,
) -> None:
    """Apply exact, non-overlapping text changes as one filesystem transaction."""
    root = project_root.resolve()
    normalized_changes = tuple(
        _normalize_change(root, change) for change in changes
    )
    if not normalized_changes:
        raise ValueError("changes must not be empty")

    paths = tuple(dict.fromkeys(change.path for change in normalized_changes))
    store = snapshot_store or SnapshotStore(root, paths)
    store.snapshot_before_apply(paths)
    prepared = _prepare_changes(root, normalized_changes)

    replace_file = replace or os.replace
    temporary_files: list[Path] = []
    replacement_started = False
    try:
        for relative_path, content in prepared.items():
            temporary_files.append(
                _write_temporary(root / relative_path, content)
            )

        for temporary_file, relative_path in zip(
            temporary_files, prepared, strict=True
        ):
            replacement_started = True
            replace_file(temporary_file, root / relative_path)
    except OSError:
        if replacement_started:
            store.restore_pre_apply()
        raise
    finally:
        for temporary_file in temporary_files:
            temporary_file.unlink(missing_ok=True)


def _normalize_change(project_root: Path, change: Change) -> Change:
    if not isinstance(change, Change):
        raise TypeError("changes must contain Change instances")
    normalized = normalize_rel_path(project_root, change.path)
    relative_path = normalized.relative_to(project_root).as_posix()
    return Change(relative_path, change.old_text, change.new_text)


def _prepare_changes(
    project_root: Path,
    changes: tuple[Change, ...],
) -> dict[str, str]:
    by_path: dict[str, list[tuple[int, int, str]]] = {}
    contents: dict[str, str] = {}
    for change in changes:
        if change.path not in contents:
            contents[change.path] = (
                project_root / change.path
            ).read_text(encoding="utf-8")

        content = contents[change.path]
        start = _find_exact_match(content, change.old_text)
        by_path.setdefault(change.path, []).append(
            (start, start + len(change.old_text), change.new_text)
        )

    prepared: dict[str, str] = {}
    for relative_path, spans in by_path.items():
        ordered = sorted(spans)
        if any(
            current[0] < previous[1]
            for previous, current in zip(ordered, ordered[1:])
        ):
            raise ValueError("changes must not overlap")
        content = contents[relative_path]
        for start, end, new_text in reversed(ordered):
            content = content[:start] + new_text + content[end:]
        prepared[relative_path] = content
    return prepared


def _find_exact_match(content: str, old_text: str) -> int:
    if not old_text:
        raise ValueError("old_text must not be empty")
    start = content.find(old_text)
    if start < 0 or content.find(old_text, start + 1) >= 0:
        raise ValueError("old_text must match exactly once")
    return start


def _write_temporary(target: Path, content: str) -> Path:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.safefix-",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        return temporary_path
    except OSError:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
