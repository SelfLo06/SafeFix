from pathlib import Path

import pytest

from safefix.guardrail import Guardrail
from safefix.models import Change, GuardDecision, ToolCall, ToolName


def patch_call(*changes: Change) -> ToolCall:
    return ToolCall(tool=ToolName.APPLY_PATCH, changes=changes)


def test_test_file_edit_is_permanently_denied(tmp_path: Path):
    guardrail = Guardrail(tmp_path, writable_paths={"tests/test_app.py"})

    decision = guardrail.check(patch_call(Change("tests/test_app.py", "old", "new")))

    assert decision is GuardDecision.DENY


def test_unknown_or_stub_action_is_denied(tmp_path: Path):
    guardrail = Guardrail(tmp_path)

    assert guardrail.check(object()) is GuardDecision.DENY
    assert guardrail.check(ToolCall(tool=ToolName.APPLY_PATCH)) is GuardDecision.DENY


@pytest.mark.parametrize("path", ["../outside.py", ".git/config", "__pycache__/x.pyc"])
def test_read_path_escape_and_hard_denies_are_permanently_denied(
    tmp_path: Path, path: str
):
    guardrail = Guardrail(tmp_path)

    assert guardrail.check(ToolCall(tool=ToolName.READ_FILE, path=path)) is GuardDecision.DENY


def test_read_symlink_escape_is_permanently_denied(tmp_path: Path):
    outside = tmp_path.parent / "outside-read-target.py"
    outside.write_text("secret = True\n", encoding="utf-8")
    link = tmp_path / "link.py"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")

    guardrail = Guardrail(tmp_path)

    assert guardrail.check(ToolCall(tool=ToolName.READ_FILE, path="link.py")) is GuardDecision.DENY


def test_write_policy_denies_non_writable_path(tmp_path: Path):
    (tmp_path / "src").mkdir()
    guardrail = Guardrail(tmp_path, writable_paths={"src/app.py"})

    assert guardrail.check(patch_call(Change("docs/readme.md", "old", "new"))) is GuardDecision.DENY


def test_three_files_and_eighty_changed_lines_are_allowed(tmp_path: Path):
    (tmp_path / "src").mkdir()
    for name in "abc":
        (tmp_path / "src" / f"{name}.py").write_text("old\n")
    guardrail = Guardrail(tmp_path, writable_paths={"src/a.py", "src/b.py", "src/c.py"})
    changes = tuple(Change(path, "old", "x\n" * count) for path, count in (
        ("src/a.py", 19), ("src/b.py", 29), ("src/c.py", 29)
    ))

    assert guardrail.check(patch_call(*changes)) is GuardDecision.ALLOW


def test_more_than_three_files_or_eighty_lines_requires_approval(tmp_path: Path):
    (tmp_path / "src").mkdir()
    for name in "abcd":
        (tmp_path / "src" / f"{name}.py").write_text("old\n")
    guardrail = Guardrail(
        tmp_path,
        writable_paths={f"src/{name}.py" for name in "abcd"},
    )

    four_files = patch_call(*(Change(f"src/{name}.py", "old", "x") for name in "abcd"))
    eighty_one_lines = patch_call(Change("src/a.py", "old", "x\n" * 81))

    assert guardrail.check(four_files) is GuardDecision.REQUIRE_APPROVAL
    assert guardrail.check(eighty_one_lines) is GuardDecision.REQUIRE_APPROVAL


def test_invalid_match_or_overlap_is_permanently_denied(tmp_path: Path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "app.py").write_text("abcdef\n")
    guardrail = Guardrail(tmp_path, writable_paths={"src/app.py"})

    assert guardrail.check(patch_call(Change("src/app.py", "missing", "x"))) is GuardDecision.DENY
    assert guardrail.check(
        patch_call(
            Change("src/app.py", "abcdef", "one"),
            Change("src/app.py", "cdef", "two"),
        )
    ) is GuardDecision.DENY
