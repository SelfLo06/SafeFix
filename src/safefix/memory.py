"""Capped, opt-in project repair summaries."""

import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone
import re


MAX_MEMORY_ENTRIES = 20
MAX_MEMORY_ENTRY_CHARS = 500
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")


class MemoryFormatError(ValueError):
    """Raised when opt-in project memory is not valid SafeFix JSON."""


class ProjectMemoryStore:
    """Persist bounded public summaries separately for each project."""

    def __init__(self, project_root: Path, data_dir: Path | None = None) -> None:
        self.project_id = hashlib.sha256(
            str(project_root.resolve()).encode("utf-8")
        ).hexdigest()[:16]
        root = data_dir or Path(
            os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
        )
        self.path = root / "safefix" / "memory" / f"{self.project_id}.json"

    def update(
        self,
        summary: str,
        *,
        unsuccessful_patch_fingerprints: list[str] | tuple[str, ...] = (),
    ) -> None:
        previous = self._payload().get("recent_unsuccessful_patch_fingerprints", [])
        fingerprints = list(previous)
        incoming = list(unsuccessful_patch_fingerprints)
        if not all(
            isinstance(item, str) and _FINGERPRINT_RE.fullmatch(item)
            for item in incoming
        ):
            raise MemoryFormatError("patch fingerprints have an invalid format")
        fingerprints.extend(incoming)
        fingerprints = fingerprints[-MAX_MEMORY_ENTRIES:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "project_id": self.project_id,
                    "last_session_summary": sanitize_summary(summary),
                    "recent_unsuccessful_patch_fingerprints": fingerprints,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    def load(self, *, use_memory: bool = False) -> tuple[str, ...]:
        if not use_memory or not self.path.exists():
            return ()
        payload = self._payload()
        original_summary = payload["last_session_summary"]
        summary = sanitize_summary(original_summary)
        if summary != original_summary:
            self.update(summary)
        return (summary,) if summary else ()

    def load_fingerprints(self, *, use_memory: bool = False) -> tuple[str, ...]:
        if not use_memory or not self.path.exists():
            return ()
        return tuple(self._payload()["recent_unsuccessful_patch_fingerprints"])

    def _payload(self) -> dict[str, object]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MemoryFormatError("project memory is unreadable") from exc
        if not isinstance(payload, dict) or payload.get("project_id") != self.project_id:
            raise MemoryFormatError("project memory has an invalid project identity")
        summary = payload.get("last_session_summary")
        fingerprints = payload.get("recent_unsuccessful_patch_fingerprints")
        updated_at = payload.get("updated_at")
        if (
            not isinstance(summary, str)
            or len(summary) > MAX_MEMORY_ENTRY_CHARS
            or not isinstance(fingerprints, list)
            or len(fingerprints) > MAX_MEMORY_ENTRIES
            or not all(
                isinstance(item, str) and _FINGERPRINT_RE.fullmatch(item)
                for item in fingerprints
            )
            or not isinstance(updated_at, str)
        ):
            raise MemoryFormatError("project memory has an invalid schema")
        return payload


def sanitize_summary(summary: str) -> str:
    normalized = " ".join(summary.split())
    if "traceback" in normalized.lower():
        return "[redacted traceback]"
    normalized = re.sub(r"(?i)\bsk-[A-Za-z0-9_-]{10,}", "sk-[redacted]", normalized)
    normalized = re.sub(r"(?i)bearer\s+\S+", "Bearer [redacted]", normalized)
    normalized = re.sub(r"(?i)\bAIza[0-9A-Za-z_-]{20,}", "[redacted]", normalized)
    normalized = re.sub(
        r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{10,}\b",
        "[redacted]",
        normalized,
    )
    normalized = re.sub(
        r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}\b",
        "[redacted]",
        normalized,
    )
    normalized = re.sub(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b", "[redacted]", normalized)
    normalized = re.sub(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b", "[redacted]", normalized)
    normalized = re.sub(r"\bAKIA[0-9A-Z]{16}\b", "[redacted]", normalized)
    if re.search(r"(?m)\b(def|class|import|from)\b|[{}]", normalized):
        return "[redacted source-like summary]"
    return re.sub(
        r"(?i)(api[_-]?key|authorization|token|secret|password|passwd|private[_-]?key|credential)\s*[:=]\s*\S+",
        r"\1=[redacted]",
        normalized,
    )[:MAX_MEMORY_ENTRY_CHARS]
