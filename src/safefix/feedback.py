from safefix.models import FailureSet, Feedback


class FeedbackEngine:
    def evaluate(
        self,
        baseline: FailureSet,
        best: FailureSet,
        current: FailureSet,
    ) -> Feedback:
        if not current.ids:
            outcome = "success"
        elif current.ids - baseline.ids:
            outcome = "worse"
        elif (current.ids & baseline.ids) < best.ids:
            outcome = "better"
        elif (current.ids & baseline.ids) == best.ids:
            outcome = "same"
        else:
            outcome = "worse"

        introduced = current.ids - baseline.ids
        resolved = baseline.ids - current.ids
        return Feedback(
            outcome=outcome,
            labels={
                "baseline_count": str(len(baseline.ids)),
                "best_count": str(len(best.ids)),
                "current_count": str(len(current.ids)),
                "introduced_count": str(len(introduced)),
                "resolved_count": str(len(resolved)),
            },
        )
