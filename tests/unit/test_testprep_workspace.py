from pathlib import Path

import pytest

from safefix.testprep.models import GeneratedTestCandidate
from safefix.testprep.workspace import CandidateWorkspace


def candidate(candidate_id: str = "c1") -> GeneratedTestCandidate:
    return GeneratedTestCandidate(
        candidate_id=candidate_id,
        test_source="def test_generated_value():\n    assert True\n",
        basis="The public contract requires this behavior.",
        sources=("src/app.py",),
    )


def test_stage_writes_only_inside_session_workspace_and_cleanup_is_confined(
    tmp_path: Path,
):
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir()
    source.write_text("VALUE = 1\n", encoding="utf-8")
    existing_test = tmp_path / "tests" / "test_existing.py"
    existing_test.parent.mkdir()
    existing_test.write_text("def test_existing():\n    assert True\n", encoding="utf-8")
    outside = tmp_path.parent / "session-sentinel.txt"
    outside.write_text("keep", encoding="utf-8")

    workspace = CandidateWorkspace(tmp_path, "session-1")
    staged = workspace.stage(candidate())
    accepted = workspace.accepted_path(candidate())

    assert staged.is_file()
    assert staged.read_text(encoding="utf-8") == candidate().test_source
    assert staged.is_relative_to(workspace.session_root)
    assert accepted.is_relative_to(workspace.session_root)
    assert not accepted.exists()
    assert source.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert existing_test.read_text(encoding="utf-8").startswith("def test_existing")

    workspace.cleanup()

    assert not workspace.session_root.exists()
    assert source.exists()
    assert existing_test.exists()
    assert outside.read_text(encoding="utf-8") == "keep"


def test_candidate_and_session_ids_cannot_escape_workspace(tmp_path: Path):
    workspace = CandidateWorkspace(tmp_path, "session-1")

    with pytest.raises(ValueError, match="candidate_id"):
        workspace.stage(candidate("../escape"))

    with pytest.raises(ValueError, match="session_id"):
        CandidateWorkspace(tmp_path, "../escape")


def test_cleanup_does_not_remove_project_files(tmp_path: Path):
    project_file = tmp_path / "keep.txt"
    project_file.write_text("keep", encoding="utf-8")
    workspace = CandidateWorkspace(tmp_path, "session-1")
    workspace.stage(candidate())

    workspace.cleanup()

    assert project_file.read_text(encoding="utf-8") == "keep"


def test_workspace_rejects_symlinked_project_workspace_and_session_components(
    tmp_path: Path,
):
    real_project = tmp_path / "project"
    real_project.mkdir()
    project_link = tmp_path / "project-link"
    project_link.symlink_to(real_project, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        CandidateWorkspace(project_link, "session-1")

    workspace_link_project = tmp_path / "workspace-link-project"
    workspace_link_project.mkdir()
    outside_workspace = tmp_path / "outside-workspace"
    outside_workspace.mkdir()
    (workspace_link_project / ".safefix").symlink_to(
        outside_workspace, target_is_directory=True
    )

    with pytest.raises(ValueError, match="symlink"):
        CandidateWorkspace(workspace_link_project, "session-1")

    session_link_project = tmp_path / "session-link-project"
    session_link_project.mkdir()
    sessions = session_link_project / ".safefix" / "sessions"
    sessions.mkdir(parents=True)
    outside_session = tmp_path / "outside-session"
    outside_session.mkdir()
    (sessions / "session-1").symlink_to(outside_session, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        CandidateWorkspace(session_link_project, "session-1")


def test_workspace_refuses_preexisting_session_ownership_collision(tmp_path: Path):
    session_root = tmp_path / ".safefix" / "sessions" / "session-1"
    session_root.mkdir(parents=True)
    sentinel = session_root / "unowned.txt"
    sentinel.write_text("keep", encoding="utf-8")

    workspace = CandidateWorkspace(tmp_path, "session-1")

    with pytest.raises(ValueError, match="owned"):
        workspace.stage(candidate())
    with pytest.raises(ValueError, match="owned"):
        workspace.cleanup()
    assert sentinel.read_text(encoding="utf-8") == "keep"
