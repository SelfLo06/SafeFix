from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Callable, Iterable, Mapping

from .paths import normalize_rel_path


Replace = Callable[[Path, Path], None]


class SnapshotStore:
    """Keep text snapshots for a fixed set of existing project files."""

    def __init__(
        self,
        project_root: Path,
        paths: Iterable[str | Path],
        *,
        replace: Replace | None = None,
    ) -> None:
        self._project_root = project_root.resolve()
        self._paths = tuple(dict.fromkeys(self._relative_path(path) for path in paths))
        self._replace = replace or os.replace
        self.baseline_contents: dict[str, str] = {}
        self.best_contents: dict[str, str] = {}
        self.pre_apply_contents: dict[str, str] | None = None

    def snapshot_before_apply(
        self,
        paths: Iterable[str | Path] | None = None,
    ) -> dict[str, str]:
        selected = self._selected_paths(paths)
        first_touched = tuple(
            relative_path
            for relative_path in selected
            if relative_path not in self.baseline_contents
        )
        self.baseline_contents.update(self._read_contents(first_touched))
        self.pre_apply_contents = self._read_contents(selected)
        return dict(self.pre_apply_contents)

    def update_best(self) -> None:
        """Record only touched files whose current contents differ from baseline."""
        current = self._read_contents(self.baseline_contents)
        self.best_contents = {
            relative_path: content
            for relative_path, content in current.items()
            if content != self.baseline_contents[relative_path]
        }

    def restore(self, contents: Mapping[str, str] | None = None) -> None:
        target_contents: dict[str, str] = {}
        if contents is None:
            source_contents = {
                relative_path: self.best_contents.get(relative_path, baseline)
                for relative_path, baseline in self.baseline_contents.items()
            }
        else:
            source_contents = contents
        for path, content in source_contents.items():
            relative_path = self._relative_path(path)
            if relative_path in target_contents:
                raise ValueError("restore contents contain duplicate paths")
            target_contents[relative_path] = content
        selected = tuple(target_contents)
        self._validate_selected_paths(selected)

        temporary_files: dict[str, Path] = {}
        backups: dict[str, Path] = {}
        try:
            for relative_path, content in target_contents.items():
                target = self._absolute_path(relative_path)
                temporary_files[relative_path] = self._write_temporary(target, content)
                backups[relative_path] = self._write_temporary(
                    target, target.read_text(encoding="utf-8")
                )

            try:
                for relative_path in target_contents:
                    self._replace(
                        temporary_files[relative_path],
                        self._absolute_path(relative_path),
                    )
            except OSError:
                for relative_path, backup in backups.items():
                    os.replace(backup, self._absolute_path(relative_path))
                raise
        finally:
            for temporary_file in (*temporary_files.values(), *backups.values()):
                temporary_file.unlink(missing_ok=True)

    def restore_pre_apply(self) -> None:
        if self.pre_apply_contents is None:
            raise RuntimeError("pre-apply snapshot has not been captured")
        self.restore(self.pre_apply_contents)

    def _selected_paths(self, paths: Iterable[str | Path] | None) -> tuple[str, ...]:
        selected = self._paths if paths is None else tuple(
            dict.fromkeys(self._relative_path(path) for path in paths)
        )
        self._validate_selected_paths(selected)
        return selected

    def _validate_selected_paths(self, paths: Iterable[str]) -> None:
        known_paths = set(self._paths)
        unknown = set(paths) - known_paths
        if unknown:
            raise ValueError(f"path is not tracked: {next(iter(unknown))}")

    def _read_contents(self, paths: Iterable[str]) -> dict[str, str]:
        return {
            relative_path: self._absolute_path(relative_path).read_text(encoding="utf-8")
            for relative_path in paths
        }

    def _relative_path(self, path: str | Path) -> str:
        candidate = Path(path)
        if candidate.is_absolute():
            resolved = candidate.resolve()
            try:
                return resolved.relative_to(self._project_root).as_posix()
            except ValueError as exc:
                raise ValueError("path escapes project root") from exc
        return normalize_rel_path(self._project_root, str(path)).relative_to(
            self._project_root
        ).as_posix()

    def _absolute_path(self, relative_path: str) -> Path:
        return self._project_root / relative_path

    def _write_temporary(self, target: Path, content: str) -> Path:
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
