from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Callable

from ..models import Change
from ..patch_preflight import prepare_changes
from ..paths import is_write_denied, normalize_rel_path
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

    paths = tuple(
        dict.fromkeys(root / change.path for change in normalized_changes)
    )
    store = snapshot_store or SnapshotStore(root, paths)
    store.snapshot_before_apply(paths)
    prepared = prepare_changes(root, normalized_changes)

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
    if normalized.suffix != ".py" or is_write_denied(project_root, relative_path):
        raise ValueError("write denied for path")
    return Change(relative_path, change.old_text, change.new_text)


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
