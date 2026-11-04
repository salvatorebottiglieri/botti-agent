"""Validation result models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from cortex.memory.models import Concept, Fact


@dataclass
class ValidationResult:
    """The outcome of checking a concept derivation against the loaded facts."""

    valid: bool
    conflicting_facts: list[Fact] = field(default_factory=list)
    suggested_fix: str | None = None


DerivationStatus = Literal["valid", "rejected"]


@dataclass
class DerivationOutcome:
    """The outcome of a derivation attempt, including any LLM revisions."""

    status: DerivationStatus
    concept: Concept | None = None
    rejection_reason: str | None = None
    attempts: int = 0
