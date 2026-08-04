from pathlib import Path
from importlib import import_module

import pytest

from safefix.models import Change
from safefix.snapshot import SnapshotStore
from safefix.tools.apply_patch import apply_patch


apply_patch_module = import_module("safefix.tools.apply_patch")


def _project_with_files(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "src"
    source.mkdir()
    first = source / "first.py"
    second = source / "second.py"
    first.write_text("first baseline\n")
    second.write_text("second baseline\n")
    return first, second


@pytest.mark.parametrize(
    ("content", "old_text"),
    [("first baseline\n", "missing"), ("repeated\nrepeated\n", "repeated")],
)
def test_apply_patch_requires_exactly_one_old_text_match(
    tmp_path: Path,
    content: str,
    old_text: str,
):
    first, _ = _project_with_files(tmp_path)
    first.write_text(content)

    with pytest.raises(ValueError, match="exactly once"):
        apply_patch(tmp_path, [Change("src/first.py", old_text, "replacement")])

    assert first.read_text() == content


def test_apply_patch_replaces_an_exact_match(tmp_path: Path):
    first, _ = _project_with_files(tmp_path)

    apply_patch(
        tmp_path,
        [Change("src/first.py", "first baseline", "updated first")],
    )

    assert first.read_text() == "updated first\n"


def test_apply_patch_rejects_overlapping_changes_before_writing(tmp_path: Path):
    first, _ = _project_with_files(tmp_path)
    first.write_text("abcdef\n")

    with pytest.raises(ValueError, match="overlap"):
        apply_patch(
            tmp_path,
            [
                Change("src/first.py", "abcdef", "first"),
                Change("src/first.py", "cdef", "second"),
            ],
        )

    assert first.read_text() == "abcdef\n"


def test_apply_patch_applies_multiple_non_overlapping_changes(tmp_path: Path):
    first, _ = _project_with_files(tmp_path)
    first.write_text("first and second\n")

    apply_patch(
        tmp_path,
        [
            Change("src/first.py", "first", "one"),
            Change("src/first.py", "second", "two"),
        ],
    )

    assert first.read_text() == "one and two\n"


def test_apply_patch_prechecks_all_changes_before_any_write(tmp_path: Path):
    first, second = _project_with_files(tmp_path)
    first.write_text("pre-apply first\n")
    store = SnapshotStore(tmp_path, ["src/first.py", "src/second.py"])
    changes = [
        Change("src/first.py", "pre-apply first", "updated first"),
        Change("src/second.py", "not present", "updated second"),
    ]

    with pytest.raises(ValueError, match="exactly once"):
        apply_patch(tmp_path, changes, store)

    assert first.read_text() == "pre-apply first\n"
    assert second.read_text() == "second baseline\n"
    assert store.pre_apply_contents == {
        "src/first.py": "pre-apply first\n",
        "src/second.py": "second baseline\n",
    }


def test_apply_patch_restores_pre_apply_contents_when_replace_fails(
    tmp_path: Path,
):
    first, second = _project_with_files(tmp_path)
    replacements = 0

    def fail_on_second_replacement(source: Path, target: Path) -> None:
        nonlocal replacements
        replacements += 1
        if replacements == 2:
            raise OSError("injected replacement failure")
        source.replace(target)

    changes = [
        Change("src/first.py", "first baseline", "updated first"),
        Change("src/second.py", "second baseline", "updated second"),
    ]

    with pytest.raises(OSError, match="injected replacement failure"):
        apply_patch(tmp_path, changes, replace=fail_on_second_replacement)

    assert first.read_text() == "first baseline\n"
    assert second.read_text() == "second baseline\n"
    assert list((tmp_path / "src").glob(".*.safefix-*")) == []


def test_apply_patch_cleans_temporary_file_when_fsync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    first, _ = _project_with_files(tmp_path)

    def fail_fsync(_: int) -> None:
        raise OSError("injected fsync failure")

    monkeypatch.setattr(apply_patch_module.os, "fsync", fail_fsync)

    with pytest.raises(OSError, match="injected fsync failure"):
        apply_patch(
            tmp_path,
            [Change("src/first.py", "first baseline", "updated first")],
        )

    assert first.read_text() == "first baseline\n"
    assert list((tmp_path / "src").glob(".*.safefix-*")) == []
