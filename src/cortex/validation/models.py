"""Validation result models."""

from __future__ import annotations

from dataclasses import dataclass, field

from cortex.memory.models import Fact


@dataclass
class ValidationResult:
    """The outcome of checking a concept derivation against the loaded facts."""

    valid: bool
    conflicting_facts: list[Fact] = field(default_factory=list)
    suggested_fix: str | None = None
