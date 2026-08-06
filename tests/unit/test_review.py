import json

import pytest

from safefix.models import ReviewVerdict
from safefix.review import ReviewModelClient, ReviewParseError, ReviewParser, ReviewResult


def review_payload(**overrides):
    payload = {
        "verdict": "PASS",
        "basis_supported": True,
        "invented_behavior": False,
        "implementation_coupling": False,
        "risk": "low",
        "summary": "The candidate is grounded in documented public behavior.",
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_parser_returns_structured_review_result():
    result = ReviewParser().parse(review_payload())

    assert result == ReviewResult(
        verdict=ReviewVerdict.PASS,
        basis_supported=True,
        invented_behavior=False,
        implementation_coupling=False,
        risk="low",
        summary="The candidate is grounded in documented public behavior.",
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"verdict": "WARN"},
        {"verdict": "PASS", "basis_supported": "yes"},
        {"verdict": "PASS", "invented_behavior": 0},
        {"verdict": "NOPE"},
        {"verdict": "PASS", "basis_supported": True, "invented_behavior": False,
         "implementation_coupling": False, "risk": "low", "summary": "ok", "extra": 1},
    ],
)
def test_parser_rejects_missing_wrong_or_unknown_fields(payload):
    with pytest.raises(ReviewParseError):
        ReviewParser().parse(json.dumps(payload))


def test_parser_rejects_non_object_trailing_and_non_finite_json():
    parser = ReviewParser()

    with pytest.raises(ReviewParseError):
        parser.parse("[]")
    with pytest.raises(ReviewParseError):
        parser.parse(review_payload() + " {}")
    with pytest.raises(ReviewParseError):
        parser.parse('{"verdict":"PASS","basis_supported":true,"invented_behavior":false,'
                     '"implementation_coupling":false,"risk":NaN,"summary":"ok"}')


def test_parser_does_not_chain_raw_malformed_response_or_secrets():
    secret = "Authorization: Bearer sk-proj-review-secret"

    with pytest.raises(ReviewParseError) as exc_info:
        ReviewParser().parse(
            '{"verdict":"PASS","basis_supported":true,"invented_behavior":false,'
            f'"implementation_coupling":false,"risk":"low","summary":"{secret}"'
        )

    error = exc_info.value
    assert error.__cause__ is None
    assert error.__context__ is None
    assert secret not in str(error)


def test_parser_rejects_oversized_response_and_text_fields():
    parser = ReviewParser()

    with pytest.raises(ReviewParseError):
        parser.parse("{" + "x" * (ReviewParser.MAX_RESPONSE_BYTES + 1) + "}")
    with pytest.raises(ReviewParseError):
        parser.parse(review_payload(summary="x" * (ReviewParser.MAX_SUMMARY_CHARS + 1)))


def test_parser_accepts_wire_case_for_enum_verdicts():
    result = ReviewParser().parse(review_payload(verdict="review_required"))

    assert result.verdict is ReviewVerdict.REVIEW_REQUIRED


def test_review_model_client_parses_the_role_scoped_client_response():
    class FakeLLM:
        def __init__(self):
            self.prompts = []

        def complete(self, prompt):
            self.prompts.append(prompt)
            return review_payload(verdict="WARN", risk="medium", summary="display")

    llm = FakeLLM()
    client = ReviewModelClient(llm)

    result = client.review("candidate review prompt")

    assert result.verdict is ReviewVerdict.WARN
    assert result.risk == "medium"
    assert llm.prompts == ["candidate review prompt"]
