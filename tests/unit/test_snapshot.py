from pathlib import Path

import pytest

from safefix.snapshot import SnapshotStore


def _project_with_files(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "src"
    source.mkdir()
    first = source / "first.py"
    second = source / "second.py"
    first.write_text("first baseline\n")
    second.write_text("second baseline\n")
    return first, second


def test_baseline_contents_capture_initial_files(tmp_path: Path):
    _project_with_files(tmp_path)

    store = SnapshotStore(tmp_path, ["src/first.py", "src/second.py"])

    assert store.baseline_contents == {
        "src/first.py": "first baseline\n",
        "src/second.py": "second baseline\n",
    }


def test_best_contents_start_as_a_copy_of_baseline(tmp_path: Path):
    _project_with_files(tmp_path)

    store = SnapshotStore(tmp_path, ["src/first.py", "src/second.py"])
    (tmp_path / "src/first.py").write_text("changed\n")

    assert store.best_contents == {
        "src/first.py": "first baseline\n",
        "src/second.py": "second baseline\n",
    }


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

    assert first.read_text() == "first baseline\n"
    assert second.read_text() == "second baseline\n"
