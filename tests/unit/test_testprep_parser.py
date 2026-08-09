import json

import pytest

from safefix.testprep.parser import (
    MAX_CANDIDATES,
    MAX_RESPONSE_BYTES,
    CandidateParser,
    ParseError,
)


def candidate_payload(**overrides):
    candidate = {
        "candidate_id": "c1",
        "test_source": "def test_value():\n    assert parse('x') == 'x'\n",
        "basis": "The public parser contract documents this behavior.",
        "sources": ["src/app.py"],
    }
    candidate.update(overrides)
    return {"candidates": [candidate]}


def test_parses_one_documented_candidate_and_normalizes_sources():
    result = CandidateParser().parse(json.dumps(candidate_payload(sources=["./src/../src/app.py"])))

    assert len(result) == 1
    assert result[0].candidate_id == "c1"
    assert result[0].test_source.startswith("def test_value")
    assert result[0].basis.startswith("The public parser")
    assert result[0].sources == ("src/app.py",)
    assert result[0].touched_existing_tests == ()


def test_parses_declared_coverage_ids():
    result = CandidateParser().parse(
        json.dumps(candidate_payload(covers=["behavior-1", "behavior-2"]))
    )

    assert result[0].covers == ("behavior-1", "behavior-2")


def test_rejects_duplicate_coverage_ids():
    with pytest.raises(ParseError, match="coverage IDs must be unique"):
        CandidateParser().parse(json.dumps(candidate_payload(covers=["behavior-1", "behavior-1"])))


def test_candidate_requires_basis_and_sources():
    response = json.dumps(
        {
            "candidates": [
                {
                    "candidate_id": "c1",
                    "test_source": "def test_value(): assert parse('x') == 'x'",
                    "basis": "",
                    "sources": [],
                }
            ]
        }
    )

    with pytest.raises(ParseError):
        CandidateParser().parse(response)


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        [{"candidate_id": "c1"}],
        {"candidates": [{"candidate_id": "c1", "test_source": "x", "basis": "b", "sources": [], "extra": 1}]},
        {"candidate": []},
        {"candidates": {"candidate_id": "c1"}},
        {"candidates": [{"candidate_id": "c1", "test_source": "", "basis": "b", "sources": ["src/app.py"]}]},
    ],
)
def test_rejects_malformed_or_unknown_candidate_shapes(payload):
    response = payload if isinstance(payload, str) else json.dumps(payload)

    with pytest.raises(ParseError):
        CandidateParser().parse(response)


@pytest.mark.parametrize("source", ["../app.py", "src/../../app.py", "/tmp/app.py", "C:/app.py", "\\\\server\\app.py"])
def test_rejects_source_paths_that_escape_or_are_absolute(source):
    with pytest.raises(ParseError):
        CandidateParser().parse(json.dumps(candidate_payload(sources=[source])))


def test_rejects_existing_test_writes_and_duplicate_candidate_ids():
    payload = {
        "candidates": [
            candidate_payload()["candidates"][0] | {"touched_existing_tests": ["tests/test_existing.py"]},
            candidate_payload()["candidates"][0],
        ]
    }

    with pytest.raises(ParseError):
        CandidateParser().parse(json.dumps(payload))


def test_rejects_oversized_response():
    oversized = "x" * MAX_RESPONSE_BYTES

    with pytest.raises(ParseError):
        CandidateParser().parse(oversized)


def test_rejects_too_many_candidates():
    candidate = candidate_payload()["candidates"][0]
    response = json.dumps(
        {"candidates": [candidate | {"candidate_id": f"c{i}"} for i in range(MAX_CANDIDATES + 1)]}
    )

    with pytest.raises(ParseError):
        CandidateParser().parse(response)


def test_rejects_deep_but_size_bounded_json_with_deterministic_parse_error():
    response = '{"candidates":' + ("[" * 10_000) + ("]" * 10_000) + "}"

    with pytest.raises(
        ParseError, match=r"^response JSON nesting exceeds the parser limit$"
    ):
        CandidateParser().parse(response)
