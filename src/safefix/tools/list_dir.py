from pathlib import Path

from .read_file import _readable_path


def list_dir(project_root: Path, path: str = ".") -> list[str]:
    """List readable direct children as sorted project-relative paths."""
    root = project_root.resolve()
    directory = _readable_path(root, path)
    if not directory.is_dir():
        raise NotADirectoryError(path)

    entries: list[str] = []
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        try:
            readable = _readable_path(root, entry.relative_to(root).as_posix())
        except ValueError:
            continue
        entries.append(readable.relative_to(root).as_posix())
    return entries
