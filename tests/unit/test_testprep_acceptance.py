from pathlib import Path

import pytest

from safefix.models import AcceptanceMode, CandidateStatus, ReviewVerdict
from safefix.review import ReviewResult
from safefix.testprep.acceptance import AcceptanceDecision, CandidateAcceptancePolicy
from safefix.testprep.stability import CandidateEvaluation


DISTINCT_PAIRS = (("https://test.example/v1", "test-model"), ("https://review.example/v1", "review-model"))
SAME_PAIRS = (("https://same.example/v1", "model"), ("https://same.example/v1", "model"))


def review(**overrides):
    values = {
        "verdict": ReviewVerdict.PASS,
        "basis_supported": True,
        "invented_behavior": False,
        "implementation_coupling": False,
        "risk": "low",
        "summary": "supported",
    }
    values.update(overrides)
    return ReviewResult(**values)


def evaluation(status: CandidateStatus) -> CandidateEvaluation:
    return CandidateEvaluation(
        candidate=Path("candidate.py"),
        status=status,
        runs=(),
        stable_failure_ids=frozenset({"candidate::test_behavior"})
        if status is CandidateStatus.FAIL
        else frozenset(),
        reason="scripted",
    )


@pytest.mark.parametrize("mode", [AcceptanceMode.REVIEW, AcceptanceMode.STANDARD, AcceptanceMode.HIGH_RISK])
def test_error_and_flaky_are_rejected_without_manual_approval(mode):
    policy = CandidateAcceptancePolicy()

    for status in (CandidateStatus.ERROR, CandidateStatus.FLAKY):
        decision = policy.decide(
            mode=mode,
            evaluation=evaluation(status),
            review_result=None,
            existing_test_count=0,
            model_identity_pairs=DISTINCT_PAIRS,
            auto_accept_count=0,
            cap=5,
        )
        assert decision == AcceptanceDecision(
            accepted=False,
            requires_manual=False,
            reason=f"candidate evaluation is {status.value}",
            automatic=False,
        )


def test_review_mode_requires_manual_for_stable_pass_and_fail():
    policy = CandidateAcceptancePolicy()

    for status in (CandidateStatus.PASS, CandidateStatus.FAIL):
        decision = policy.decide(
            AcceptanceMode.REVIEW,
            evaluation(status),
            review(),
            0,
            DISTINCT_PAIRS,
            0,
            5,
        )
        assert decision.accepted is False
        assert decision.requires_manual is True
        assert decision.automatic is False


def test_standard_accepts_pass_automatically_but_fail_requires_manual():
    policy = CandidateAcceptancePolicy()

    passed = policy.decide(AcceptanceMode.STANDARD, evaluation(CandidateStatus.PASS), None, 3, (), 0, 5)
    failed = policy.decide(AcceptanceMode.STANDARD, evaluation(CandidateStatus.FAIL), review(), 3, (), 0, 5)

    assert passed == AcceptanceDecision(True, False, "stable PASS accepted automatically", True)
    assert failed.accepted is False
    assert failed.requires_manual is True
    assert failed.automatic is False


def test_high_risk_accepts_eligible_fail_automatically():
    decision = CandidateAcceptancePolicy().decide(
        AcceptanceMode.HIGH_RISK,
        evaluation(CandidateStatus.FAIL),
        review(),
        existing_test_count=0,
        model_identity_pairs=DISTINCT_PAIRS,
        auto_accept_count=0,
        cap=5,
    )

    assert decision == AcceptanceDecision(True, False, "eligible stable FAIL accepted automatically", True)


@pytest.mark.parametrize(
    ("review_result", "existing_test_count", "pairs", "auto_accept_count", "cap"),
    [
        (review(verdict=ReviewVerdict.WARN), 0, DISTINCT_PAIRS, 0, 5),
        (review(verdict=ReviewVerdict.REVIEW_REQUIRED), 0, DISTINCT_PAIRS, 0, 5),
        (review(basis_supported=False), 0, DISTINCT_PAIRS, 0, 5),
        (review(invented_behavior=True), 0, DISTINCT_PAIRS, 0, 5),
        (review(implementation_coupling=True), 0, DISTINCT_PAIRS, 0, 5),
        (review(risk="high"), 0, DISTINCT_PAIRS, 0, 5),
        (review(), 1, DISTINCT_PAIRS, 0, 5),
        (review(), 0, SAME_PAIRS, 0, 5),
        (review(), 0, DISTINCT_PAIRS, 5, 5),
    ],
)
def test_high_risk_fail_downgrades_to_manual_when_any_gate_fails(
    review_result, existing_test_count, pairs, auto_accept_count, cap
):
    decision = CandidateAcceptancePolicy().decide(
        AcceptanceMode.HIGH_RISK,
        evaluation(CandidateStatus.FAIL),
        review_result,
        existing_test_count,
        pairs,
        auto_accept_count,
        cap,
    )

    assert decision.accepted is False
    assert decision.requires_manual is True
    assert decision.automatic is False
    assert decision.reason.startswith("high-risk automatic acceptance downgraded:")


def test_high_risk_fail_without_review_is_manual():
    decision = CandidateAcceptancePolicy().decide(
        AcceptanceMode.HIGH_RISK,
        evaluation(CandidateStatus.FAIL),
        None,
        0,
        DISTINCT_PAIRS,
        0,
        5,
    )

    assert decision.accepted is False
    assert decision.requires_manual is True
    assert "Review Model" in decision.reason


def test_high_risk_pass_remains_automatic_and_does_not_need_review():
    decision = CandidateAcceptancePolicy().decide(
        AcceptanceMode.HIGH_RISK,
        evaluation(CandidateStatus.PASS),
        None,
        4,
        SAME_PAIRS,
        99,
        0,
    )

    assert decision == AcceptanceDecision(True, False, "stable PASS accepted automatically", True)
