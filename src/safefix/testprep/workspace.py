from __future__ import annotations

from pathlib import Path
import shutil

from .models import GeneratedTestCandidate


class CandidateWorkspace:
    """Own the pre-baseline filesystem area for generated test candidates."""

    def __init__(self, root: str | Path, session_id: str) -> None:
        self.root = Path(root).resolve()
        self.session_id = _safe_component(session_id, "session_id")
        self.session_root = self.root / ".safefix" / "sessions" / self.session_id
        self._assert_confined(self.session_root)

    def stage(self, candidate: GeneratedTestCandidate) -> Path:
        path = self._candidate_path("staged", candidate)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._assert_confined(path.parent)
        path.write_text(candidate.test_source, encoding="utf-8")
        self._assert_confined(path)
        return path

    def accepted_path(self, candidate: GeneratedTestCandidate) -> Path:
        """Return the session-owned destination used for accepted promotion."""
        return self._candidate_path("accepted", candidate)

    def cleanup(self) -> None:
        """Remove only this session's generated-candidate directory."""
        self._assert_confined(self.session_root)
        if self.session_root.exists():
            shutil.rmtree(self.session_root)

    def _candidate_path(
        self, area: str, candidate: GeneratedTestCandidate
    ) -> Path:
        candidate_id = _safe_component(candidate.candidate_id, "candidate_id")
        path = self.session_root / area / f"{candidate_id}.py"
        self._assert_confined(path)
        return path

    def _assert_confined(self, path: Path) -> None:
        resolved_root = self.root.resolve()
        resolved_session = self.session_root.resolve()
        try:
            resolved_session.relative_to(resolved_root)
            path.resolve().relative_to(resolved_session)
        except ValueError as exc:
            raise ValueError("candidate workspace path escapes the project root") from exc


def _safe_component(value: str, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or any(character in value for character in "/\\:\x00")
    ):
        raise ValueError(f"{field} must be a safe path component")
    return value
