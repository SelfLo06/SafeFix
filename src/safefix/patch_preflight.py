from pathlib import Path

from .models import Change


def prepare_changes(
    project_root: Path,
    changes: tuple[Change, ...],
) -> dict[str, str]:
    """Validate exact matches and prepare all target contents before writing."""
    by_path: dict[str, list[tuple[int, int, str]]] = {}
    contents: dict[str, str] = {}
    for change in changes:
        if change.path not in contents:
            contents[change.path] = (
                project_root / change.path
            ).read_text(encoding="utf-8")

        content = contents[change.path]
        start = _find_exact_match(content, change.old_text)
        by_path.setdefault(change.path, []).append(
            (start, start + len(change.old_text), change.new_text)
        )

    prepared: dict[str, str] = {}
    for relative_path, spans in by_path.items():
        ordered = sorted(spans)
        if any(
            current[0] < previous[1]
            for previous, current in zip(ordered, ordered[1:])
        ):
            raise ValueError("changes must not overlap")
        content = contents[relative_path]
        for start, end, new_text in reversed(ordered):
            content = content[:start] + new_text + content[end:]
        prepared[relative_path] = content
    return prepared


def _find_exact_match(content: str, old_text: str) -> int:
    if not old_text:
        raise ValueError("old_text must not be empty")
    start = content.find(old_text)
    if start < 0 or content.find(old_text, start + 1) >= 0:
        raise ValueError("old_text must match exactly once")
    return start
