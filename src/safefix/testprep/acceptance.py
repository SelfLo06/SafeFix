from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from ..models import AcceptanceMode, CandidateStatus, ReviewVerdict
from ..review import ReviewResult
from .stability import CandidateEvaluation


@dataclass(frozen=True)
class AcceptanceDecision:
    accepted: bool
    requires_manual: bool
    reason: str
    automatic: bool


class CandidateAcceptancePolicy:
    """Pure acceptance policy for already parsed and statically checked candidates."""

    def decide(
        self,
        mode: AcceptanceMode,
        evaluation: CandidateEvaluation,
        review_result: ReviewResult | None,
        existing_test_count: int,
        model_identity_pairs: Sequence[tuple[str, str]] | Mapping[str, tuple[str, str]],
        auto_accept_count: int,
        cap: int,
    ) -> AcceptanceDecision:
        mode = AcceptanceMode(mode)
        if evaluation.status in {CandidateStatus.ERROR, CandidateStatus.FLAKY}:
            return AcceptanceDecision(
                accepted=False,
                requires_manual=False,
                reason=f"candidate evaluation is {evaluation.status.value}",
                automatic=False,
            )

        if evaluation.status is CandidateStatus.PASS:
            return AcceptanceDecision(
                accepted=True,
                requires_manual=False,
                reason="stable PASS accepted automatically",
                automatic=True,
            ) if mode is not AcceptanceMode.REVIEW else self._manual(
                "review mode requires manual approval"
            )

        if evaluation.status is not CandidateStatus.FAIL:
            raise ValueError(f"unsupported candidate status: {evaluation.status!r}")
        if mode is not AcceptanceMode.HIGH_RISK:
            return self._manual(
                "review mode requires manual approval"
                if mode is AcceptanceMode.REVIEW
                else "standard mode requires manual approval for stable FAIL"
            )

        failures = self._high_risk_failures(
            review_result,
            existing_test_count,
            model_identity_pairs,
            auto_accept_count,
            cap,
        )
        if failures:
            return self._manual(
                "high-risk automatic acceptance downgraded: " + "; ".join(failures)
            )
        return AcceptanceDecision(
            accepted=True,
            requires_manual=False,
            reason="eligible stable FAIL accepted automatically",
            automatic=True,
        )

    @staticmethod
    def _manual(reason: str) -> AcceptanceDecision:
        return AcceptanceDecision(
            accepted=False,
            requires_manual=True,
            reason=reason,
            automatic=False,
        )

    @staticmethod
    def _high_risk_failures(
        review_result: ReviewResult | None,
        existing_test_count: int,
        model_identity_pairs: Sequence[tuple[str, str]] | Mapping[str, tuple[str, str]],
        auto_accept_count: int,
        cap: int,
    ) -> list[str]:
        failures: list[str] = []
        if review_result is None:
            failures.append("Review Model approval is missing")
        else:
            if review_result.verdict is not ReviewVerdict.PASS:
                failures.append("Review Model did not approve")
            if not review_result.basis_supported or review_result.invented_behavior:
                failures.append("basis is unsupported or invents behavior")
            if review_result.implementation_coupling:
                failures.append("candidate is coupled to implementation details")
            if review_result.risk.strip().lower() != "low":
                failures.append("Review Model risk is not low")

        if existing_test_count != 0:
            failures.append("existing tests require manual verification")
        if not _has_distinct_model_identities(model_identity_pairs):
            failures.append("Test and Review model identities are not distinct")
        if auto_accept_count >= cap:
            failures.append("automatic-failure cap is exhausted")
        return failures


def _has_distinct_model_identities(
    pairs: Sequence[tuple[str, str]] | Mapping[str, tuple[str, str]],
) -> bool:
    if isinstance(pairs, Mapping):
        test_pair = pairs.get("test")
        review_pair = pairs.get("review")
    else:
        if len(pairs) != 2:
            return False
        test_pair, review_pair = pairs
    if not _valid_identity_pair(test_pair) or not _valid_identity_pair(review_pair):
        return False
    return _effective_identity_pair(test_pair) != _effective_identity_pair(review_pair)


def _effective_identity_pair(pair: tuple[str, str]) -> tuple[str, str]:
    return (pair[0].rstrip("/"), pair[1])


def _valid_identity_pair(pair: object) -> bool:
    return (
        isinstance(pair, tuple)
        and len(pair) == 2
        and all(isinstance(part, str) and bool(part.strip()) for part in pair)
    )
