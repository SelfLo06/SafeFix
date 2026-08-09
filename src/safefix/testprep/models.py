from dataclasses import dataclass


@dataclass(frozen=True)
class GeneratedTestCandidate:
    candidate_id: str
    test_source: str
    basis: str
    sources: tuple[str, ...]
    touched_existing_tests: tuple[str, ...] = ()
    covers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(
            self, "touched_existing_tests", tuple(self.touched_existing_tests)
        )
        object.__setattr__(self, "covers", tuple(self.covers))


@dataclass(frozen=True)
class CoverageRequirement:
    requirement_id: str
    behavior: str
    source_path: str | None = None
    required_lines: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not self.requirement_id.strip() or not self.behavior.strip():
            raise ValueError("coverage requirements need an ID and behavior")
        if self.source_path is not None and not self.source_path.strip():
            raise ValueError("coverage requirement source path cannot be empty")
        if any(type(line) is not int or line <= 0 for line in self.required_lines):
            raise ValueError("coverage requirement lines must be positive integers")


@dataclass(frozen=True)
class RuleViolation:
    code: str
    message: str
