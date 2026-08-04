from safefix.models import FailureSet, Feedback


class FeedbackEngine:
    def evaluate(self, previous: FailureSet, current: FailureSet) -> Feedback:
        if not current.ids:
            outcome = "success"
        elif current.ids < previous.ids:
            outcome = "better"
        elif current.ids == previous.ids:
            outcome = "same"
        elif previous.ids < current.ids:
            outcome = "worse"
        else:
            outcome = "incomparable"

        return Feedback(outcome=outcome)
