from pathlib import Path

from ..paths import _is_hard_denied, normalize_rel_path


def read_file(project_root: Path, path: str) -> str:
    """Read one UTF-8 project file after applying the read policy."""
    file_path = _readable_path(project_root, path)
    return file_path.read_text(encoding="utf-8")


def _readable_path(project_root: Path, path: str) -> Path:
    root = project_root.resolve()
    try:
        resolved = normalize_rel_path(root, path)
    except ValueError as exc:
        raise ValueError("path must remain within the project root") from exc
    if _is_hard_denied(root, resolved):
        raise ValueError("path is not readable")
    return resolved
