import json
import posixpath
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from .models import GeneratedTestCandidate


MAX_RESPONSE_BYTES = 64 * 1024
MAX_CANDIDATES = 20
MAX_SOURCE_CHARS = 32 * 1024
MAX_BASIS_CHARS = 4 * 1024
MAX_SOURCE_REFERENCES = 32
MAX_SOURCE_REFERENCE_CHARS = 512
_TOP_LEVEL_FIELDS = {"candidates"}
_CANDIDATE_FIELDS = {
    "candidate_id",
    "test_source",
    "basis",
    "sources",
    "touched_existing_tests",
    "covers",
}


class ParseError(ValueError):
    """Raised when a model response is not one bounded candidate list."""


class _DuplicateField(ValueError):
    pass


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"unsupported JSON constant: {value}")


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateField(key)
        result[key] = value
    return result


class CandidateParser:
    """Parse exactly one bounded JSON object containing candidate records."""

    def parse(self, response: str) -> tuple[GeneratedTestCandidate, ...]:
        if not isinstance(response, str):
            raise ParseError("response must be a JSON string")
        try:
            response_size = len(response.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise ParseError("response is not valid UTF-8 text") from exc
        if response_size > MAX_RESPONSE_BYTES:
            raise ParseError("response exceeds the candidate output limit")

        try:
            payload = json.loads(
                response,
                object_pairs_hook=_object_pairs,
                parse_constant=_reject_json_constant,
            )
        except RecursionError as exc:
            raise ParseError("response JSON nesting exceeds the parser limit") from exc
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ParseError("response must be valid JSON") from exc

        if not isinstance(payload, dict) or set(payload) != _TOP_LEVEL_FIELDS:
            raise ParseError("response must contain only a candidates field")
        raw_candidates = payload["candidates"]
        if not isinstance(raw_candidates, list):
            raise ParseError("candidates must be an array")
        if len(raw_candidates) > MAX_CANDIDATES:
            raise ParseError("candidate list exceeds the candidate count limit")

        parsed: list[GeneratedTestCandidate] = []
        seen_ids: set[str] = set()
        for raw_candidate in raw_candidates:
            candidate = self._parse_candidate(raw_candidate)
            if candidate.candidate_id in seen_ids:
                raise ParseError("candidate IDs must be unique")
            seen_ids.add(candidate.candidate_id)
            parsed.append(candidate)
        return tuple(parsed)

    def _parse_candidate(self, raw_candidate: Any) -> GeneratedTestCandidate:
        if not isinstance(raw_candidate, dict):
            raise ParseError("each candidate must be an object")
        if set(raw_candidate) - _CANDIDATE_FIELDS:
            raise ParseError("candidate contains unknown fields")
        required = {"candidate_id", "test_source", "basis", "sources"}
        if not required.issubset(raw_candidate):
            raise ParseError("candidate is missing required fields")

        candidate_id = self._non_empty_text(raw_candidate["candidate_id"], "candidate_id")
        test_source = self._non_empty_text(raw_candidate["test_source"], "test_source")
        basis = self._non_empty_text(raw_candidate["basis"], "basis")
        if len(test_source) > MAX_SOURCE_CHARS:
            raise ParseError("test_source exceeds the candidate source limit")
        if len(basis) > MAX_BASIS_CHARS:
            raise ParseError("basis exceeds the candidate basis limit")

        sources = self._source_references(raw_candidate["sources"])
        raw_touched = raw_candidate.get("touched_existing_tests", [])
        if not isinstance(raw_touched, list) or any(
            not isinstance(item, str) for item in raw_touched
        ):
            raise ParseError("touched_existing_tests must be an array of strings")
        if raw_touched:
            raise ParseError("candidates cannot write existing tests")
        raw_covers = raw_candidate.get("covers", [])
        if not isinstance(raw_covers, list) or any(
            not isinstance(item, str) or not item.strip() for item in raw_covers
        ):
            raise ParseError("covers must be an array of non-empty strings")
        if len(set(raw_covers)) != len(raw_covers):
            raise ParseError("coverage IDs must be unique")

        return GeneratedTestCandidate(
            candidate_id=candidate_id,
            test_source=test_source,
            basis=basis,
            sources=sources,
            touched_existing_tests=(),
            covers=tuple(raw_covers),
        )

    def _source_references(self, value: Any) -> tuple[str, ...]:
        if not isinstance(value, list) or not value:
            raise ParseError("sources must be a non-empty array")
        if len(value) > MAX_SOURCE_REFERENCES:
            raise ParseError("sources exceed the reference count limit")
        normalized: list[str] = []
        for source in value:
            if not isinstance(source, str) or not source.strip():
                raise ParseError("source references must be non-empty strings")
            if len(source) > MAX_SOURCE_REFERENCE_CHARS:
                raise ParseError("source reference exceeds the path limit")
            normalized.append(self._normalize_source(source))
        if len(set(normalized)) != len(normalized):
            raise ParseError("source references must be unique")
        return tuple(normalized)

    @staticmethod
    def _non_empty_text(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ParseError(f"{field} must be a non-empty string")
        return value

    @staticmethod
    def _normalize_source(value: str) -> str:
        portable = value.replace("\\", "/")
        windows = PureWindowsPath(value)
        if (
            PurePosixPath(portable).is_absolute()
            or windows.is_absolute()
            or bool(windows.drive)
        ):
            raise ParseError("source references must be project-relative")
        normalized = posixpath.normpath(portable)
        if normalized == ".." or normalized.startswith("../"):
            raise ParseError("source reference escapes the project root")
        return normalized
