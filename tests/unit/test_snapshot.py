from pathlib import Path
from importlib import import_module

import pytest

from safefix.snapshot import SnapshotStore


snapshot_module = import_module("safefix.snapshot")


def _project_with_files(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "src"
    source.mkdir()
    first = source / "first.py"
    second = source / "second.py"
    first.write_text("first baseline\n")
    second.write_text("second baseline\n")
    return first, second


def test_baseline_contents_capture_first_touched_files(tmp_path: Path):
    _project_with_files(tmp_path)

    store = SnapshotStore(tmp_path, ["src/first.py", "src/second.py"])
    assert store.baseline_contents == {}

    store.snapshot_before_apply(["src/first.py"])
    (tmp_path / "src/first.py").write_text("first changed after snapshot\n")

    assert store.baseline_contents == {
        "src/first.py": "first baseline\n",
    }


def test_best_contents_start_empty(tmp_path: Path):
    _project_with_files(tmp_path)

    store = SnapshotStore(tmp_path, ["src/first.py", "src/second.py"])

    assert store.best_contents == {}


def test_restore_writes_requested_contents(tmp_path: Path):
    first, second = _project_with_files(tmp_path)
    store = SnapshotStore(tmp_path, ["src/first.py", "src/second.py"])

    store.restore({
        "src/first.py": "restored first\n",
        "src/second.py": "restored second\n",
    })

    assert first.read_text() == "restored first\n"
    assert second.read_text() == "restored second\n"


def test_snapshot_before_apply_captures_current_contents(tmp_path: Path):
    first, second = _project_with_files(tmp_path)
    store = SnapshotStore(tmp_path, ["src/first.py", "src/second.py"])
    first.write_text("before apply\n")

    captured = store.snapshot_before_apply()
    second.write_text("after apply\n")

    assert captured == {
        "src/first.py": "before apply\n",
        "src/second.py": "second baseline\n",
    }
    assert store.pre_apply_contents == captured


def test_default_restore_only_touches_files_seen_by_snapshot(tmp_path: Path):
    first, second = _project_with_files(tmp_path)
    store = SnapshotStore(tmp_path, ["src/first.py", "src/second.py"])

    store.snapshot_before_apply(["src/first.py"])
    first.write_text("patched first\n")
    second.write_text("unrelated external change\n")

    store.restore()

    assert first.read_text() == "first baseline\n"
    assert second.read_text() == "unrelated external change\n"


def test_restore_failure_leaves_all_files_unchanged(tmp_path: Path):
    first, second = _project_with_files(tmp_path)
    replacements = 0

    def fail_on_second_replacement(source: Path, target: Path) -> None:
        nonlocal replacements
        replacements += 1
        if replacements == 2:
            raise OSError("injected replacement failure")
        source.replace(target)

    store = SnapshotStore(
        tmp_path,
        ["src/first.py", "src/second.py"],
        replace=fail_on_second_replacement,
    )

    with pytest.raises(OSError, match="injected replacement failure"):
        store.restore({
            "src/first.py": "new first\n",
            "src/second.py": "new second\n",
        })

    assert replacements == 4
    assert first.read_text() == "first baseline\n"
    assert second.read_text() == "second baseline\n"


def test_restore_cleans_temporary_file_when_fsync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    first, second = _project_with_files(tmp_path)
    store = SnapshotStore(tmp_path, ["src/first.py", "src/second.py"])

    def fail_fsync(_: int) -> None:
        raise OSError("injected fsync failure")

    monkeypatch.setattr(snapshot_module.os, "fsync", fail_fsync)

    with pytest.raises(OSError, match="injected fsync failure"):
        store.restore({
            "src/first.py": "new first\n",
            "src/second.py": "new second\n",
        })

    assert first.read_text() == "first baseline\n"
    assert second.read_text() == "second baseline\n"
    assert list((tmp_path / "src").glob(".*.safefix-*")) == []
