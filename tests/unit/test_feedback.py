from safefix.feedback import FeedbackEngine
from safefix.models import FailureSet


def failures(*ids: str) -> FailureSet:
    return FailureSet(frozenset(ids))


def test_feedback_is_better_when_current_failures_are_a_strict_subset():
    feedback = FeedbackEngine().evaluate(
        failures("a", "b"), failures("a", "b"), failures("a")
    )

    assert feedback.outcome == "better"
    assert feedback.labels == {
        "baseline_count": "2",
        "best_count": "2",
        "current_count": "1",
        "introduced_count": "0",
        "resolved_count": "1",
    }


def test_feedback_is_same_when_failure_sets_are_equal_and_nonempty():
    feedback = FeedbackEngine().evaluate(
        failures("a", "b"), failures("a"), failures("a")
    )

    assert feedback.outcome == "same"


def test_feedback_is_worse_when_current_failures_strictly_grow():
    feedback = FeedbackEngine().evaluate(
        failures("a", "b"), failures("a"), failures("a", "b")
    )

    assert feedback.outcome == "worse"


def test_feedback_is_success_when_current_failure_set_is_empty():
    feedback = FeedbackEngine().evaluate(
        failures("a"), failures("a"), failures()
    )

    assert feedback.outcome == "success"


def test_feedback_treats_incomparable_failure_sets_as_worse():
    feedback = FeedbackEngine().evaluate(
        failures("a", "b", "c"), failures("a", "b"), failures("a", "c")
    )

    assert feedback.outcome == "worse"


def test_feedback_treats_new_failure_as_worse_even_when_initial_failures_shrink():
    feedback = FeedbackEngine().evaluate(
        failures("a", "b"), failures("a", "b"), failures("a", "c")
    )

    assert feedback.outcome == "worse"
