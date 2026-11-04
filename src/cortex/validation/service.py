"""Synchronous concept validation service."""

from __future__ import annotations

from cortex.memory.models import ConceptDerivation, Fact
from cortex.validation.logic_engine import LogicEngine
from cortex.validation.models import ValidationResult


class ConceptValidationService:
    """Delegates symbolic validation to a ``LogicEngine`` instance."""

    def __init__(self, logic_engine: LogicEngine) -> None:
        self._logic_engine = logic_engine

    def validate(self, concept_derivation: ConceptDerivation) -> ValidationResult:
        """Return the engine's verdict for ``concept_derivation``."""
        return self._logic_engine.validate_derivation(concept_derivation)

    def load_facts(self, facts: list[Fact]) -> None:
        """Replace the engine's loaded axioms with ``facts``."""
        self._logic_engine.load_facts(facts)
