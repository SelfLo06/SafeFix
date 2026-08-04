from safefix.feedback import FeedbackEngine
from safefix.models import FailureSet


def failures(*ids: str) -> FailureSet:
    return FailureSet(frozenset(ids))


def test_feedback_is_better_when_current_failures_are_a_strict_subset():
    feedback = FeedbackEngine().evaluate(failures("a", "b"), failures("a"))

    assert feedback.outcome == "better"


def test_feedback_is_same_when_failure_sets_are_equal_and_nonempty():
    feedback = FeedbackEngine().evaluate(failures("a"), failures("a"))

    assert feedback.outcome == "same"


def test_feedback_is_worse_when_current_failures_strictly_grow():
    feedback = FeedbackEngine().evaluate(failures("a"), failures("a", "b"))

    assert feedback.outcome == "worse"


def test_feedback_is_success_when_current_failure_set_is_empty():
    feedback = FeedbackEngine().evaluate(failures("a"), failures())

    assert feedback.outcome == "success"


def test_feedback_is_incomparable_when_failure_sets_change_in_both_directions():
    feedback = FeedbackEngine().evaluate(failures("a", "b"), failures("a", "c"))

    assert feedback.outcome == "incomparable"
