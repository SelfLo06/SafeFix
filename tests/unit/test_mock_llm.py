import pytest

from safefix.llm.mock import MockLLM, ScriptExhaustedError


def test_mock_llm_returns_scripted_responses_in_order():
    client = MockLLM(["first action", "second action"])

    assert client.complete("first prompt") == "first action"
    assert client.complete("second prompt") == "second action"


def test_mock_llm_raises_the_same_error_after_script_exhaustion():
    client = MockLLM(["only action"])

    assert client.complete("first prompt") == "only action"
    with pytest.raises(ScriptExhaustedError, match="scripted responses exhausted"):
        client.complete("second prompt")
    with pytest.raises(ScriptExhaustedError, match="scripted responses exhausted"):
        client.complete("third prompt")
