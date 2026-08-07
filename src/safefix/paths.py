from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath
import posixpath


_VIRTUAL_ENV_DIRS = {".venv", "venv", "env", "virtualenv", "virtualenvs"}
_CACHE_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox", "cache", ".cache"}
_INTERNAL_DIRS = {".safefix"}
_SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".crt"}
_SECRET_NAMES = {".env", "env.lock", "credential", "credentials", "secret", "secrets", "id_rsa"}


def normalize_rel_path(project_root: Path, rel: str) -> Path:
    """Resolve a project-relative path, rejecting absolute paths and escapes."""
    relative = Path(rel)
    if relative.is_absolute():
        raise ValueError("path must be project-relative")

    root = project_root.resolve()
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("path escapes project root") from exc
    return resolved


def is_read_denied(project_root: Path, rel_path: str) -> bool:
    try:
        resolved = normalize_rel_path(project_root, rel_path)
    except ValueError:
        return True
    return _is_hard_denied(project_root.resolve(), resolved)


def is_write_denied(project_root: Path, rel_path: str) -> bool:
    try:
        resolved = normalize_rel_path(project_root, rel_path)
    except ValueError:
        return True
    root = project_root.resolve()
    return _is_hard_denied(root, resolved) or _is_test_source(root, resolved)


def is_write_denied_path(project_root: Path, resolved_path: Path) -> bool:
    """Check write policy for a path already normalized at a trust boundary."""
    root = project_root.resolve()
    return _is_hard_denied(root, resolved_path) or _is_test_source(root, resolved_path)


def is_read_path_obviously_denied(rel_path: str) -> bool:
    """Reject lexical read-path hazards before filesystem resolution."""
    if not isinstance(rel_path, str) or not rel_path:
        return True
    portable = rel_path.replace("\\", "/")
    if PurePosixPath(portable).is_absolute() or PureWindowsPath(rel_path).is_absolute():
        return True
    normalized = posixpath.normpath(portable)
    if normalized == ".." or normalized.startswith("../"):
        return True
    return _hard_denied_parts(PurePosixPath(normalized).parts)


def compute_writable_py_files(
    project_root: Path,
    allowed_paths: list[str] | None,
    excluded_paths: list[str],
) -> set[Path]:
    """Return existing Python files permitted by the configured write policy."""
    root = project_root.resolve()
    excluded = [normalize_rel_path(root, path) for path in excluded_paths]

    if allowed_paths is None:
        search_roots = [root / "src"] if (root / "src").is_dir() else [root]
    else:
        search_roots = []
        for path in allowed_paths:
            normalized = normalize_rel_path(root, path)
            if _is_hard_denied(root, normalized) or _is_test_source(root, normalized):
                raise ValueError("explicit path is write-denied")
            search_roots.append(normalized)

    candidates: set[Path] = set()
    for search_root in search_roots:
        if search_root.is_file():
            if search_root.suffix == ".py":
                candidates.add(search_root)
        elif search_root.is_dir():
            candidates.update(path.resolve() for path in search_root.glob("**/*.py"))

    return {
        path
        for path in candidates
        if _inside(path, root)
        and not _is_hard_denied(root, path)
        and not _is_test_source(root, path)
        and not any(_inside(path, excluded_path) for excluded_path in excluded)
    }


def _is_hard_denied(root: Path, path: Path) -> bool:
    relative_parts = path.relative_to(root).parts
    return _hard_denied_parts(relative_parts)


def _hard_denied_parts(relative_parts: tuple[str, ...]) -> bool:
    if ".git" in relative_parts or any(part in _INTERNAL_DIRS for part in relative_parts):
        return True
    if any(part in _VIRTUAL_ENV_DIRS or part in _CACHE_DIRS for part in relative_parts):
        return True
    return any(_is_secret(part) for part in relative_parts)


def _is_test_source(root: Path, path: Path) -> bool:
    relative_parts = path.relative_to(root).parts
    name = path.name
    return (
        "tests" in relative_parts
        or (path.suffix == ".py" and name.startswith("test_"))
        or (path.suffix == ".py" and path.stem.endswith("_test"))
    )


def _is_secret(name: str) -> bool:
    lower_name = name.lower()
    return (
        lower_name in _SECRET_NAMES
        or lower_name.startswith(".env.")
        or lower_name.endswith(tuple(_SECRET_SUFFIXES))
        or lower_name.startswith("credential.")
        or lower_name.startswith("credentials.")
        or lower_name.startswith("secret.")
        or lower_name.startswith("secrets.")
    )


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
