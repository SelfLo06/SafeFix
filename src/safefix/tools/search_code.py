from pathlib import Path
import os

from ..paths import is_read_denied
from .read_file import _readable_path


Match = tuple[str, int, str]
MAX_SEARCH_MATCHES = 100


def search_code(
    project_root: Path,
    path: str = ".",
    query: str | None = None,
) -> list[Match]:
    """Find substring matches in readable files under a project path."""
    if query is None:
        raise ValueError("search_code requires a path and query")
    if not isinstance(query, str) or not query:
        raise ValueError("query must be a non-empty string")

    root = project_root.resolve()
    target = _readable_path(root, path)
    if not target.exists():
        raise FileNotFoundError(path)
    if target.is_file():
        files = [(target, target.relative_to(root).as_posix())]
    elif target.is_dir():
        files = []
        for current, directories, filenames in os.walk(
            target, onerror=_raise_walk_error
        ):
            current_path = Path(current)
            directories[:] = sorted(
                name
                for name in directories
                if not is_read_denied(
                    root, (current_path / name).relative_to(root).as_posix()
                )
            )
            files.extend(
                (current_path / name, None)
                for name in sorted(filenames)
            )
    else:
        raise ValueError("search path must be a file or directory")
    matches: list[Match] = []
    for file_path, relative in files:
        if not file_path.is_file():
            continue
        if relative is None:
            relative = file_path.relative_to(root).as_posix()
            if is_read_denied(root, relative):
                continue
            readable = _readable_path(root, relative)
        else:
            readable = file_path
        lines = readable.read_text(encoding="utf-8").splitlines()
        for line_number, line in enumerate(lines, start=1):
            if query in line:
                matches.append((relative, line_number, line))
                if len(matches) >= MAX_SEARCH_MATCHES:
                    return matches
    return matches


def _raise_walk_error(error: OSError) -> None:
    raise error
