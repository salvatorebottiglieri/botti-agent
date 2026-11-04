"""Tests for ConceptValidationService delegation."""

from __future__ import annotations

from cortex.memory.models import Concept, ConceptDerivation, Fact, FactType
from cortex.validation.logic_engine import LogicEngine
from cortex.validation.models import ValidationResult
from cortex.validation.service import ConceptValidationService


class RecordingEngine(LogicEngine):
    """A LogicEngine that records calls and returns a fixed result."""

    def __init__(self, result: ValidationResult) -> None:
        super().__init__()
        self.result = result
        self.seen: list[ConceptDerivation] = []

    def validate_derivation(self, derivation: ConceptDerivation) -> ValidationResult:
        self.seen.append(derivation)
        return self.result


class TestServiceDelegation:
    """ConceptValidationService is a pure synchronous delegation seam."""

    def test_validate_passes_the_derivation_and_returns_the_engine_result(self):
        """Witness: a sentinel result proves the service does not re-implement validation."""
        sentinel = ValidationResult(
            valid=False, conflicting_facts=[], suggested_fix="from engine"
        )
        engine = RecordingEngine(sentinel)
        service = ConceptValidationService(engine)
        derivation = ConceptDerivation(concept=Concept(), proof_chain=["p(user, x)"])

        result = service.validate(derivation)

        assert result is sentinel
        assert engine.seen == [derivation]

    def test_validate_matches_a_real_engine_result(self):
        """Witness: fact location.home + proof location(user, office) -> same invalid result."""
        fact = Fact(type=FactType.LOCATION, symbolic_repr="location.home")
        engine = LogicEngine()
        engine.load_facts([fact])
        service = ConceptValidationService(engine)
        derivation = ConceptDerivation(
            concept=Concept(), proof_chain=["location(user, office)"]
        )

        result = service.validate(derivation)

        assert result.valid is False
        assert result.conflicting_facts == [fact]
