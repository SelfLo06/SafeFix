from __future__ import annotations

from dataclasses import dataclass
import json
from hashlib import sha256
from pathlib import Path
from typing import Sequence, TYPE_CHECKING

from .models import BaselineSource
from .testrunner import TestRunResult

if TYPE_CHECKING:
    from .testrunner import TestRunner


class ManifestError(ValueError):
    """Raised when a frozen test manifest or its files are invalid."""


@dataclass(frozen=True)
class ManifestEntry:
    path: str
    sha256: str
    origin: BaselineSource
    candidate_id: str | None = None


@dataclass(frozen=True)
class ExistingTestDiscovery:
    collected_ids: frozenset[str]
    collected_count: int
    result: TestRunResult


@dataclass(frozen=True)
class FrozenTestManifest:
    session_id: str
    baseline_source: BaselineSource
    entries: tuple[ManifestEntry, ...]
    stability_runs: int
    manifest_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(self.entries))

    @classmethod
    def from_paths(
        cls,
        project_root: str | Path,
        paths: Sequence[str | Path],
        baseline_source: BaselineSource,
        stability_runs: int,
    ) -> FrozenTestManifest:
        if stability_runs <= 0:
            raise ManifestError("stability_runs must be positive")
        if not paths:
            raise ManifestError("formal test manifest cannot be empty")

        root = Path(project_root).resolve()
        entries = [
            _entry_from_path(root, path, baseline_source)
            for path in paths
        ]
        entries.sort(key=lambda entry: entry.path)
        entries_tuple = tuple(entries)
        return cls(
            session_id="",
            baseline_source=BaselineSource(baseline_source),
            entries=entries_tuple,
            stability_runs=stability_runs,
            manifest_hash=_manifest_hash(
                BaselineSource(baseline_source), entries_tuple, stability_runs
            ),
        )

    def verify(self, project_root: str | Path) -> None:
        expected_hash = _manifest_hash(
            self.baseline_source, self.entries, self.stability_runs
        )
        if expected_hash != self.manifest_hash:
            raise ManifestError("manifest hash does not match its entries")

        root = Path(project_root).resolve()
        for entry in self.entries:
            path = _path_from_relative(root, entry.path)
            if not path.is_file():
                raise ManifestError(f"manifest test is missing: {entry.path}")
            try:
                content = path.read_bytes().decode("utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise ManifestError(f"cannot read manifest test: {entry.path}") from exc
            actual_hash = sha256(content.encode("utf-8")).hexdigest()
            if actual_hash != entry.sha256:
                raise ManifestError(f"manifest test changed: {entry.path}")


def discover_existing_tests(
    project_root: str | Path, runner: TestRunner
) -> ExistingTestDiscovery:
    result = runner.run()
    collected_cases = tuple(case for case in result.cases if not case.is_collection_error)
    return ExistingTestDiscovery(
        collected_ids=frozenset(case.failure_id for case in collected_cases),
        collected_count=len(collected_cases),
        result=result,
    )


def _entry_from_path(
    project_root: Path, path: str | Path, origin: BaselineSource
) -> ManifestEntry:
    normalized_path, file_path = _resolve_path(project_root, path)
    if not file_path.is_file():
        raise ManifestError(f"manifest test is missing: {normalized_path}")
    try:
        content = file_path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ManifestError(f"cannot read manifest test: {normalized_path}") from exc
    return ManifestEntry(
        path=normalized_path,
        sha256=sha256(content.encode("utf-8")).hexdigest(),
        origin=BaselineSource(origin),
    )


def _resolve_path(project_root: Path, path: str | Path) -> tuple[str, Path]:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = project_root / candidate
    try:
        resolved = candidate.resolve()
        relative = resolved.relative_to(project_root)
    except (OSError, ValueError) as exc:
        raise ManifestError(f"manifest path escapes project root: {path}") from exc
    return relative.as_posix(), resolved


def _path_from_relative(project_root: Path, path: str) -> Path:
    return _resolve_path(project_root, path)[1]


def _manifest_hash(
    baseline_source: BaselineSource,
    entries: Sequence[ManifestEntry],
    stability_runs: int,
) -> str:
    payload = {
        "baseline_source": BaselineSource(baseline_source).value,
        "entries": [
            {
                "candidate_id": entry.candidate_id,
                "origin": BaselineSource(entry.origin).value,
                "path": entry.path,
                "sha256": entry.sha256,
            }
            for entry in sorted(entries, key=lambda item: item.path)
        ],
        "stability_runs": stability_runs,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()
