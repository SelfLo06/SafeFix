import json

import pytest

from safefix.models import Change, ToolCall, ToolName
from safefix.parse import ActionParser, ParseError


def test_parses_one_apply_patch_action(tmp_path):
    payload = json.dumps(
        {
            "tool": "apply_patch",
            "changes": [
                {"path": "src/app.py", "old_text": "return 1", "new_text": "return 2"}
            ],
        }
    )

    result = ActionParser(tmp_path).parse(payload)

    assert result == ToolCall(
        tool=ToolName.APPLY_PATCH,
        changes=(Change("src/app.py", "return 1", "return 2"),),
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"tool": "read_file", "path": "/etc/passwd"},
        [{"tool": "finish", "reason": "done"}],
        {"tool": "finish", "reason": "done", "extra": True},
        {"actions": [{"tool": "finish"}, {"tool": "finish"}]},
    ],
)
def test_rejects_invalid_action_shapes(tmp_path, payload):
    with pytest.raises(ParseError):
        ActionParser(tmp_path).parse(json.dumps(payload))


def test_parses_finish_action(tmp_path):
    assert ActionParser(tmp_path).parse(
        '{"tool":"finish","reason":"all done"}'
    ) == ToolCall(tool=ToolName.FINISH, reason="all done")


def test_finish_reason_is_optional(tmp_path):
    assert ActionParser(tmp_path).parse('{"tool":"finish"}') == ToolCall(
        tool=ToolName.FINISH
    )


def test_parser_returns_normalized_project_relative_path(tmp_path):
    result = ActionParser(tmp_path).parse(
        '{"tool":"read_file","path":"./src/../src/app.py"}'
    )

    assert result.path == "src/app.py"


def test_parser_leaves_root_escape_for_guardrail_denial(tmp_path):
    result = ActionParser(tmp_path).parse(
        '{"tool":"read_file","path":"../outside.py"}'
    )

    assert result.path == "../outside.py"


def test_parser_normalizes_backslash_separators(tmp_path):
    result = ActionParser(tmp_path).parse(
        '{"tool":"read_file","path":"src\\\\app.py"}'
    )

    assert result.path == "src/app.py"
