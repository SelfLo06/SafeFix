"""Contracts and deterministic validation for generated test candidates."""

from .models import GeneratedTestCandidate, RuleViolation
from .parser import CandidateParser, ParseError
from .rules import validate_candidate
from .service import (
    CandidateAcceptanceRecord,
    PreparationRequest,
    PreparationResult,
    PreparationSummary,
    TestPreparationService,
)

__all__ = [
    "CandidateParser",
    "GeneratedTestCandidate",
    "ParseError",
    "RuleViolation",
    "CandidateAcceptanceRecord",
    "PreparationRequest",
    "PreparationResult",
    "PreparationSummary",
    "TestPreparationService",
    "validate_candidate",
]
