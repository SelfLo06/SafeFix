import json

from safefix.memory import MAX_MEMORY_ENTRIES, ProjectMemoryStore


def test_memory_not_loaded_by_default(tmp_path):
    store = ProjectMemoryStore(tmp_path / "project", data_dir=tmp_path / "data")
    store.update("resolved the parser failure")

    assert store.load() == ()


def test_use_memory_loads_capped_slice(tmp_path):
    store = ProjectMemoryStore(tmp_path / "project", data_dir=tmp_path / "data")

    for index in range(MAX_MEMORY_ENTRIES + 1):
        store.update(f"summary-{index}")

    assert store.load(use_memory=True) == tuple(
        f"summary-{index}" for index in range(1, MAX_MEMORY_ENTRIES + 1)
    )


def test_project_memory_isolation(tmp_path):
    data_dir = tmp_path / "data"
    first = ProjectMemoryStore(tmp_path / "first", data_dir=data_dir)
    second = ProjectMemoryStore(tmp_path / "second", data_dir=data_dir)
    first.update("first project only")

    assert first.project_id != second.project_id
    assert first.load(use_memory=True) == ("first project only",)
    assert second.load(use_memory=True) == ()


def test_memory_has_no_keys_or_source(tmp_path):
    store = ProjectMemoryStore(tmp_path / "project", data_dir=tmp_path / "data")
    store.update("public repair summary")

    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload == {"entries": ["public repair summary"]}
    assert "key" not in payload
    assert "source" not in payload
