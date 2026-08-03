from pathlib import Path

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


def test_write_policy_denies_non_writable_path(tmp_path: Path):
    (tmp_path / "src").mkdir()
    guardrail = Guardrail(tmp_path, writable_paths={"src/app.py"})

    assert guardrail.check(patch_call(Change("docs/readme.md", "old", "new"))) is GuardDecision.DENY


def test_three_files_and_eighty_changed_lines_are_allowed(tmp_path: Path):
    guardrail = Guardrail(tmp_path, writable_paths={"src/a.py", "src/b.py", "src/c.py"})
    changes = tuple(Change(path, "", "x\n" * count) for path, count in (
        ("src/a.py", 20), ("src/b.py", 30), ("src/c.py", 30)
    ))

    assert guardrail.check(patch_call(*changes)) is GuardDecision.ALLOW


def test_more_than_three_files_or_eighty_lines_requires_approval(tmp_path: Path):
    guardrail = Guardrail(
        tmp_path,
        writable_paths={f"src/{name}.py" for name in "abcd"},
    )

    four_files = patch_call(*(Change(f"src/{name}.py", "", "x") for name in "abcd"))
    eighty_one_lines = patch_call(Change("src/a.py", "", "x\n" * 81))

    assert guardrail.check(four_files) is GuardDecision.REQUIRE_APPROVAL
    assert guardrail.check(eighty_one_lines) is GuardDecision.REQUIRE_APPROVAL
