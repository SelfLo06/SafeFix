from pathlib import Path

from .read_file import _readable_path


MAX_LIST_ENTRIES = 100


def list_dir(project_root: Path, path: str = ".") -> list[str]:
    """List readable direct children as sorted project-relative paths."""
    root = project_root.resolve()
    directory = _readable_path(root, path)
    if not directory.is_dir():
        raise NotADirectoryError(path)

    entries: list[str] = []
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        relative = entry.relative_to(root).as_posix()
        try:
            readable = _readable_path(root, relative)
        except ValueError:
            continue
        entries.append(readable.relative_to(root).as_posix())
    return entries[:MAX_LIST_ENTRIES]
