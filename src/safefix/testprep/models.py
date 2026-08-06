from dataclasses import dataclass


@dataclass(frozen=True)
class GeneratedTestCandidate:
    candidate_id: str
    test_source: str
    basis: str
    sources: tuple[str, ...]
    touched_existing_tests: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(
            self, "touched_existing_tests", tuple(self.touched_existing_tests)
        )


@dataclass(frozen=True)
class RuleViolation:
    code: str
    message: str
