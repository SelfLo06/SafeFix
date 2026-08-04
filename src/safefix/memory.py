"""Capped, opt-in project repair summaries."""

import hashlib
import json
import os
from pathlib import Path


MAX_MEMORY_ENTRIES = 20
MAX_MEMORY_ENTRY_CHARS = 500


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

    def update(self, summary: str) -> None:
        entries = list(self._entries())
        entries.append(summary[:MAX_MEMORY_ENTRY_CHARS])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"entries": entries[-MAX_MEMORY_ENTRIES:]}) + "\n",
            encoding="utf-8",
        )

    def load(self, *, use_memory: bool = False) -> tuple[str, ...]:
        if not use_memory or not self.path.exists():
            return ()
        return self._entries()[-MAX_MEMORY_ENTRIES:]

    def _entries(self) -> tuple[str, ...]:
        if not self.path.exists():
            return ()
        return tuple(json.loads(self.path.read_text(encoding="utf-8"))["entries"])
