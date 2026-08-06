from pathlib import Path

import pytest

from safefix.models import BaselineSource
from safefix.test_manifest import (
    FrozenTestManifest,
    ManifestError,
    discover_existing_tests,
)
from safefix.testrunner import TestRunner as Runner


def test_manifest_hash_is_deterministic_for_normalized_paths(tmp_path: Path):
    test_file = tmp_path / "tests" / "test_app.py"
    test_file.parent.mkdir()
    test_file.write_text("def test_ok(): pass\n", encoding="utf-8")

    first = FrozenTestManifest.from_paths(
        tmp_path, [test_file], BaselineSource.EXISTING, stability_runs=3
    )
    second = FrozenTestManifest.from_paths(
        tmp_path, [Path("tests") / "test_app.py"], BaselineSource.EXISTING, stability_runs=3
    )

    assert first.manifest_hash == second.manifest_hash
    assert first.entries[0].path == "tests/test_app.py"


def test_manifest_rejects_changed_test_content(tmp_path: Path):
    test_file = tmp_path / "tests" / "test_app.py"
    test_file.parent.mkdir()
    test_file.write_text("def test_ok(): pass\n", encoding="utf-8")
    manifest = FrozenTestManifest.from_paths(
        tmp_path, [test_file], BaselineSource.EXISTING, stability_runs=3
    )

    test_file.write_text("def test_changed(): pass\n", encoding="utf-8")

    with pytest.raises(ManifestError):
        manifest.verify(tmp_path)


def test_manifest_rejects_missing_test_file(tmp_path: Path):
    test_file = tmp_path / "tests" / "test_app.py"
    test_file.parent.mkdir()
    test_file.write_text("def test_ok(): pass\n", encoding="utf-8")
    manifest = FrozenTestManifest.from_paths(
        tmp_path, [test_file], BaselineSource.EXISTING, stability_runs=3
    )
    test_file.unlink()

    with pytest.raises(ManifestError):
        manifest.verify(tmp_path)


def test_manifest_rejects_tampered_manifest_hash(tmp_path: Path):
    test_file = tmp_path / "tests" / "test_app.py"
    test_file.parent.mkdir()
    test_file.write_text("def test_ok(): pass\n", encoding="utf-8")
    manifest = FrozenTestManifest.from_paths(
        tmp_path, [test_file], BaselineSource.EXISTING, stability_runs=3
    )
    tampered = manifest.__class__(
        session_id=manifest.session_id,
        baseline_source=manifest.baseline_source,
        entries=manifest.entries,
        stability_runs=manifest.stability_runs,
        manifest_hash="0" * 64,
    )

    with pytest.raises(ManifestError):
        tampered.verify(tmp_path)


def test_manifest_retains_all_existing_paths_in_mixed_manifest(tmp_path: Path):
    existing = tmp_path / "tests" / "test_existing.py"
    generated = tmp_path / ".safefix" / "generated_test.py"
    existing.parent.mkdir()
    generated.parent.mkdir()
    existing.write_text("def test_existing(): pass\n", encoding="utf-8")
    generated.write_text("def test_generated(): pass\n", encoding="utf-8")

    manifest = FrozenTestManifest.from_paths(
        tmp_path,
        [existing, generated],
        BaselineSource.MIXED,
        stability_runs=3,
    )

    assert {entry.path for entry in manifest.entries} == {
        "tests/test_existing.py",
        ".safefix/generated_test.py",
    }


def test_formal_manifest_rejects_empty_entries(tmp_path: Path):
    with pytest.raises(ManifestError, match="empty"):
        FrozenTestManifest.from_paths(
            tmp_path, [], BaselineSource.EXISTING, stability_runs=3
        )


def test_existing_test_discovery_distinguishes_no_tests_from_collection_error(
    tmp_path: Path,
):
    no_tests = Path(__file__).parents[1] / "fixtures" / "projects" / "no_tests"
    no_tests_runner = Runner(no_tests, allow_empty=True)
    no_tests_result = discover_existing_tests(no_tests, no_tests_runner)

    assert no_tests_result.collected_count == 0
    assert no_tests_result.collected_ids == frozenset()
    assert no_tests_result.result.valid is True

    broken_project = tmp_path / "broken"
    broken_project.mkdir()
    (broken_project / "pytest.ini").write_text(
        "[pytest]\ntestpaths = tests\n", encoding="utf-8"
    )
    (broken_project / "tests").mkdir()
    (broken_project / "tests" / "test_broken.py").write_text(
        "def test_broken(:\n    pass\n", encoding="utf-8"
    )
    broken_result = discover_existing_tests(
        broken_project, Runner(broken_project, allow_empty=True)
    )

    assert broken_result.collected_count == 0
    assert broken_result.result.valid is False
    assert any(
        case.failure_id.startswith("collection_error::")
        for case in broken_result.result.cases
    )
