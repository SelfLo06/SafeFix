import json

import pytest

from safefix.memory import MAX_MEMORY_ENTRIES, MemoryFormatError, ProjectMemoryStore


def test_memory_not_loaded_by_default(tmp_path):
    store = ProjectMemoryStore(tmp_path / "project", data_dir=tmp_path / "data")
    store.update("resolved the parser failure")

    assert store.load() == ()


def test_use_memory_loads_capped_slice(tmp_path):
    store = ProjectMemoryStore(tmp_path / "project", data_dir=tmp_path / "data")

    for index in range(MAX_MEMORY_ENTRIES + 1):
        store.update(f"summary-{index}")

    assert store.load(use_memory=True) == (f"summary-{MAX_MEMORY_ENTRIES}",)


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
    assert payload["project_id"] == store.project_id
    assert payload["last_session_summary"] == "public repair summary"
    assert payload["recent_unsuccessful_patch_fingerprints"] == []
    assert isinstance(payload["updated_at"], str)
    assert "key" not in payload
    assert "source" not in payload


def test_memory_caps_unsuccessful_patch_fingerprints(tmp_path):
    store = ProjectMemoryStore(tmp_path / "project", data_dir=tmp_path / "data")

    store.update("summary", unsuccessful_patch_fingerprints=[f"fp-{i}" for i in range(25)])

    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert len(payload["recent_unsuccessful_patch_fingerprints"]) == 20
    assert payload["recent_unsuccessful_patch_fingerprints"][-1] == "fp-24"


def test_memory_redacts_secret_like_summary_content(tmp_path):
    store = ProjectMemoryStore(tmp_path / "project", data_dir=tmp_path / "data")

    store.update("api_key=super-secret; traceback: full details")

    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload["last_session_summary"] == "[redacted traceback]"


def test_memory_redacts_bearer_and_source_like_summary_content(tmp_path):
    store = ProjectMemoryStore(tmp_path / "project", data_dir=tmp_path / "data")

    store.update("Bearer super-secret; def repair(): return 1")

    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert "super-secret" not in payload["last_session_summary"]
    assert "def repair" not in payload["last_session_summary"]


def test_corrupt_opt_in_memory_fails_at_boundary(tmp_path):
    store = ProjectMemoryStore(tmp_path / "project", data_dir=tmp_path / "data")
    store.path.parent.mkdir(parents=True)
    store.path.write_text("not json", encoding="utf-8")

    with pytest.raises(MemoryFormatError):
        store.load(use_memory=True)


@pytest.mark.parametrize(
    "payload",
    [
        {"project_id": "wrong", "last_session_summary": "ok", "recent_unsuccessful_patch_fingerprints": [], "updated_at": "now"},
        {"project_id": "", "last_session_summary": 1, "recent_unsuccessful_patch_fingerprints": [], "updated_at": "now"},
        {"project_id": "", "last_session_summary": "ok", "recent_unsuccessful_patch_fingerprints": [1], "updated_at": "now"},
        {"project_id": "", "last_session_summary": "ok", "recent_unsuccessful_patch_fingerprints": [], "updated_at": 1},
    ],
)
def test_memory_rejects_invalid_schema(tmp_path, payload):
    store = ProjectMemoryStore(tmp_path / "project", data_dir=tmp_path / "data")
    payload["project_id"] = store.project_id if payload["project_id"] == "" else payload["project_id"]
    store.path.parent.mkdir(parents=True)
    store.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MemoryFormatError):
        store.load(use_memory=True)


def test_memory_redacts_credentials_and_private_keys(tmp_path):
    store = ProjectMemoryStore(tmp_path / "project", data_dir=tmp_path / "data")

    store.update("password=super-secret private_key=another-secret")

    rendered = store.path.read_text(encoding="utf-8")
    assert "super-secret" not in rendered
    assert "another-secret" not in rendered
