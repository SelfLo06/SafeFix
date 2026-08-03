from pathlib import Path

from .read_file import _readable_path


Match = tuple[str, int, str]


def search_code(
    project_root: Path,
    path: str = ".",
    query: str | None = None,
) -> list[Match]:
    """Find substring matches in readable files under a project path."""
    if query is None:
        query = path
        path = "."
    if not isinstance(query, str) or not query:
        raise ValueError("query must be a non-empty string")

    root = project_root.resolve()
    target = _readable_path(root, path)
    if target.is_file():
        files = [(target, target.relative_to(root).as_posix())]
    else:
        files = [(candidate, None) for candidate in sorted(target.rglob("*"))]
    matches: list[Match] = []
    for file_path, relative in files:
        if not file_path.is_file():
            continue
        try:
            if relative is None:
                relative = file_path.relative_to(root).as_posix()
                readable = _readable_path(root, relative)
            else:
                readable = file_path
            lines = readable.read_text(encoding="utf-8").splitlines()
        except (ValueError, UnicodeDecodeError):
            continue
        for line_number, line in enumerate(lines, start=1):
            if query in line:
                matches.append((relative, line_number, line))
    return matches
