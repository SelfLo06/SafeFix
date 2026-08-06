from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from .llm.base import LLMClient
from .models import ReviewVerdict


MAX_REVIEW_RESPONSE_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = MAX_REVIEW_RESPONSE_BYTES
MAX_RISK_CHARS = 256
MAX_SUMMARY_CHARS = 4 * 1024
_REVIEW_FIELDS = {
    "verdict",
    "basis_supported",
    "invented_behavior",
    "implementation_coupling",
    "risk",
    "summary",
}


@dataclass(frozen=True)
class ReviewResult:
    verdict: ReviewVerdict
    basis_supported: bool
    invented_behavior: bool
    implementation_coupling: bool
    risk: str
    summary: str


class ReviewParseError(ValueError):
    """Raised when a model response is not one bounded Review result."""


class ReviewClient(Protocol):
    def review(self, prompt: str) -> ReviewResult:
        """Review one preparation or checkpoint prompt."""


@dataclass(frozen=True)
class FinalReviewRequest:
    baseline_summary: str
    final_diff_summary: str
    changed_files: tuple[str, ...]
    constraints: str
    pytest_summary: str


class FinalReviewService:
    """Present Harness-owned final evidence to the Review Model."""

    def review(self, request: FinalReviewRequest, review_client: ReviewClient) -> ReviewResult:
        prompt = json.dumps(
            {
                "baseline_summary": request.baseline_summary,
                "final_diff_summary": request.final_diff_summary,
                "changed_files": list(request.changed_files),
                "constraints": request.constraints,
                "pytest_summary": request.pytest_summary,
            },
            sort_keys=True,
        )
        result = review_client.review(prompt)
        if not isinstance(result, ReviewResult):
            raise ReviewParseError("Review Model returned an invalid result")
        return result


class ReviewParser:
    """Parse exactly one bounded JSON Review result."""

    MAX_RESPONSE_BYTES = MAX_RESPONSE_BYTES
    MAX_RISK_CHARS = MAX_RISK_CHARS
    MAX_SUMMARY_CHARS = MAX_SUMMARY_CHARS

    def parse(self, response: str) -> ReviewResult:
        if not isinstance(response, str):
            raise ReviewParseError("response must be a JSON string")
        try:
            response_size = len(response.encode("utf-8"))
        except UnicodeEncodeError:
            response_size = None
        if response_size is None:
            raise ReviewParseError("response is not valid UTF-8 text")
        if response_size > self.MAX_RESPONSE_BYTES:
            raise ReviewParseError("response exceeds the Review output limit")

        parse_error: str | None = None
        try:
            payload = json.loads(
                response,
                object_pairs_hook=_object_pairs,
                parse_constant=_reject_json_constant,
            )
        except RecursionError:
            parse_error = "response JSON nesting exceeds the parser limit"
        except (TypeError, ValueError):
            parse_error = "response must be valid JSON"
        if parse_error is not None:
            raise ReviewParseError(parse_error)

        if not isinstance(payload, dict) or set(payload) != _REVIEW_FIELDS:
            raise ReviewParseError("response must contain exactly the Review fields")

        verdict = _parse_verdict(payload["verdict"])
        basis_supported = _parse_bool(payload["basis_supported"], "basis_supported")
        invented_behavior = _parse_bool(payload["invented_behavior"], "invented_behavior")
        implementation_coupling = _parse_bool(
            payload["implementation_coupling"], "implementation_coupling"
        )
        risk = _parse_text(payload["risk"], "risk", self.MAX_RISK_CHARS)
        summary = _parse_text(payload["summary"], "summary", self.MAX_SUMMARY_CHARS)
        return ReviewResult(
            verdict=verdict,
            basis_supported=basis_supported,
            invented_behavior=invented_behavior,
            implementation_coupling=implementation_coupling,
            risk=risk,
            summary=summary,
        )


class ReviewModelClient:
    """Adapt one role-scoped completion client to the structured Review API."""

    def __init__(self, client: LLMClient, parser: ReviewParser | None = None) -> None:
        self._client = client
        self._parser = parser if parser is not None else ReviewParser()

    def review(self, prompt: str) -> ReviewResult:
        return self._parser.parse(self._client.complete(prompt))


class _DuplicateField(ValueError):
    pass


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateField(key)
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"unsupported JSON constant: {value}")


def _parse_verdict(value: Any) -> ReviewVerdict:
    if not isinstance(value, str):
        raise ReviewParseError("verdict must be a string")
    normalized = value.strip().lower().replace("-", "_")
    try:
        verdict = ReviewVerdict(normalized)
    except ValueError:
        verdict = None
    if verdict is None:
        raise ReviewParseError("verdict must be PASS, WARN, REVIEW_REQUIRED, or NOT_CONFIGURED")
    return verdict


def _parse_bool(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise ReviewParseError(f"{field} must be a boolean")
    return value


def _parse_text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewParseError(f"{field} must be a non-empty string")
    if len(value) > limit:
        raise ReviewParseError(f"{field} exceeds the Review output limit")
    return value
