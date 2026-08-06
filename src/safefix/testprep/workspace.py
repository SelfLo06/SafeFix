from __future__ import annotations

from pathlib import Path
import secrets
import shutil

from .models import GeneratedTestCandidate


class CandidateWorkspace:
    """Own the pre-baseline filesystem area for generated test candidates."""

    def __init__(self, root: str | Path, session_id: str) -> None:
        self.root = Path(root).absolute()
        if not self.root.is_dir():
            raise ValueError("project root must be an existing directory")
        _assert_no_symlink_components(self.root, "project root")
        self.session_id = _safe_component(session_id, "session_id")
        self.workspace_root = self.root / ".safefix"
        self.sessions_root = self.workspace_root / "sessions"
        self.session_root = self.sessions_root / self.session_id
        self._prepare_parent(self.workspace_root, "workspace")
        self._prepare_parent(self.sessions_root, "session parent")
        self._assert_confined(self.session_root)
        self._owned = False
        self._owner_token: str | None = None
        self._session_identity: tuple[int, int] | None = None
        self._owner_marker = self.session_root / ".session-owner"
        if self.session_root.exists():
            if not self.session_root.is_dir():
                raise ValueError("session workspace is not a directory")
            _assert_no_symlink_components(self.session_root, "session workspace")
        else:
            try:
                self.session_root.mkdir()
            except FileExistsError:
                _assert_no_symlink_components(self.session_root, "session workspace")
            else:
                _assert_no_symlink_components(self.session_root, "session workspace")
                self._owner_token = secrets.token_hex(24)
                with self._owner_marker.open("x", encoding="utf-8") as marker:
                    marker.write(self._owner_token)
                self._session_identity = _file_identity(self.session_root)
                self._owned = True
                _OWNED_WORKSPACES[self.session_root] = self

    def stage(self, candidate: GeneratedTestCandidate) -> Path:
        self._require_owned()
        path = self._candidate_path("staged", candidate)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._assert_confined(path.parent)
        if path.is_symlink():
            raise ValueError("candidate workspace path contains a symlink")
        path.write_text(candidate.test_source, encoding="utf-8")
        self._assert_confined(path)
        return path

    def accepted_path(self, candidate: GeneratedTestCandidate) -> Path:
        """Return the session-owned destination used for accepted promotion."""
        self._require_owned()
        return self._candidate_path("accepted", candidate)

    def cleanup(self) -> None:
        """Remove only this session's generated-candidate directory."""
        self._require_owned()
        self._assert_confined(self.session_root)
        shutil.rmtree(self.session_root)
        _OWNED_WORKSPACES.pop(self.session_root, None)
        self._owned = False

    def _candidate_path(
        self, area: str, candidate: GeneratedTestCandidate
    ) -> Path:
        if area not in {"staged", "accepted"}:
            raise ValueError("candidate workspace area is invalid")
        candidate_id = _safe_component(candidate.candidate_id, "candidate_id")
        path = self.session_root / area / f"{candidate_id}.py"
        self._assert_confined(path)
        return path

    def _prepare_parent(self, path: Path, label: str) -> None:
        _assert_no_symlink_components(path, label)
        path.mkdir(parents=True, exist_ok=True)
        _assert_no_symlink_components(path, label)

    def _require_owned(self) -> None:
        if not self._owned or self._owner_token is None:
            raise ValueError("session workspace is not owned by this instance")
        if self._owner_marker.is_symlink() or not self._owner_marker.is_file():
            raise ValueError("session workspace ownership marker is missing")
        try:
            marker = self._owner_marker.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError("session workspace ownership marker is unreadable") from exc
        if marker != self._owner_token:
            raise ValueError("session workspace ownership marker does not match")
        if self._session_identity is None:
            raise ValueError("session workspace identity is missing")
        try:
            current_identity = _file_identity(self.session_root)
        except OSError as exc:
            raise ValueError("session workspace identity is unavailable") from exc
        if current_identity != self._session_identity:
            raise ValueError("session workspace identity does not match")

    def _assert_confined(self, path: Path) -> None:
        _assert_no_symlink_components(self.root, "project root")
        _assert_no_symlink_components(self.workspace_root, "workspace")
        _assert_no_symlink_components(self.sessions_root, "session parent")
        _assert_no_symlink_components(self.session_root, "session workspace")
        resolved_root = self.root.resolve()
        resolved_session = self.session_root.resolve()
        try:
            resolved_session.relative_to(resolved_root)
            path.resolve().relative_to(resolved_session)
        except ValueError as exc:
            raise ValueError("candidate workspace path escapes the project root") from exc


def _assert_no_symlink_components(path: Path, label: str) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        if current.is_symlink():
            raise ValueError(f"{label} contains a symlink component")


def _owned_workspace_for(path: Path) -> CandidateWorkspace | None:
    workspace = _OWNED_WORKSPACES.get(path.absolute())
    if workspace is None:
        return None
    try:
        workspace._require_owned()
    except ValueError:
        return None
    return workspace


def _file_identity(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino


def _safe_component(value: str, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or any(character in value for character in "/\\:\x00")
    ):
        raise ValueError(f"{field} must be a safe path component")
    return value


_OWNED_WORKSPACES: dict[Path, CandidateWorkspace] = {}
