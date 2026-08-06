"""Tests for the Memory module."""
from datetime import UTC
from uuid import uuid4

import pytest

from cortex.memory.interfaces import ConceptRepository, FactExtractor, FactRepository
from cortex.memory.models import (
    Concept,
    Fact,
    FactMutability,
    FactType,
)


class TestFactModel:
    """Tests for the Fact model."""

    def test_fact_creation(self):
        """Test creating a basic fact."""
        fact = Fact(
            type=FactType.LOCATION,
            symbolic_repr="location.home",
            natural_lang_repr="User is at home",
            payload={"latitude": 37.7749, "longitude": -122.4194},
            confidence=0.9,
        )

        assert fact.type == FactType.LOCATION
        assert fact.symbolic_repr == "location.home"
        assert fact.natural_lang_repr == "User is at home"
        assert fact.confidence == 0.9
        assert fact.is_active()
        assert fact.retracted_at is None

    def test_fact_id_auto_generated(self):
        """Test that fact IDs are auto-generated."""
        fact1 = Fact(type=FactType.USER_FACT)
        fact2 = Fact(type=FactType.USER_FACT)

        assert fact1.id is not None
        assert fact2.id is not None
        assert fact1.id != fact2.id

    def test_fact_to_dict(self):
        """Test Fact serialization."""
        fact = Fact(
            type=FactType.LOCATION,
            symbolic_repr="location.work",
            natural_lang_repr="User is at work",
            confidence=0.8,
        )

        data = fact.to_dict()
        assert data["type"] == "location"
        assert data["symbolic_repr"] == "location.work"
        assert data["confidence"] == 0.8
        assert "id" in data
        assert "created_at" in data

    def test_fact_from_dict(self):
        """Test Fact deserialization."""
        data = {
            "id": str(uuid4()),
            "type": "activity",
            "mutability": "mutable",
            "symbolic_repr": "activity.walking",
            "natural_lang_repr": "User is walking",
            "payload": {"speed": 1.5},
            "confidence": 0.7,
            "layer": 1,
            "access_count": 5,
            "last_accessed_at": "2026-04-30T10:00:00",
            "created_at": "2026-04-30T09:00:00",
            "retracted_at": None,
        }

        fact = Fact.from_dict(data)
        assert fact.type == FactType.ACTIVITY
        assert fact.symbolic_repr == "activity.walking"
        assert fact.confidence == 0.7
        assert fact.layer == 1
        assert fact.access_count == 5

    def test_fact_is_active(self):
        """Test is_active check."""
        fact = Fact(type=FactType.USER_FACT)
        assert fact.is_active()

        # Simulate retraction
        from datetime import datetime
        fact.retracted_at = datetime.now(UTC)
        assert not fact.is_active()

    def test_fact_to_search_text(self):
        """Test search text generation."""
        fact = Fact(
            symbolic_repr="weather.current",
            natural_lang_repr="The weather is sunny with a temperature of 72°F",
        )

        text = fact.to_search_text()
        assert "weather.current" in text
        assert "sunny" in text


class TestConceptModel:
    """Tests for the Concept model."""

    def test_concept_creation(self):
        """Test creating a concept."""
        concept = Concept(
            symbolic_repr="mood.happy",
            natural_lang_repr="User seems happy based on recent messages and activity",
            derivation_method="llm_inference",
            proof_chain="User used happy emojis and completed tasks on schedule",
            confidence=0.75,
            source_facts=[uuid4(), uuid4()],
        )

        assert concept.symbolic_repr == "mood.happy"
        assert concept.derivation_method == "llm_inference"
        assert len(concept.source_facts) == 2
        assert not concept.validated

    def test_concept_to_dict(self):
        """Test Concept serialization."""
        fact_id = uuid4()
        concept = Concept(
            symbolic_repr="context.busy",
            natural_lang_repr="User is busy",
            derivation_method="rule",
            proof_chain="Multiple calendar events overlap",
            confidence=0.9,
            source_facts=[fact_id],
            validated=True,
        )

        data = concept.to_dict()
        assert data["symbolic_repr"] == "context.busy"
        assert data["derivation_method"] == "rule"
        assert data["validated"]
        assert str(fact_id) in data["source_facts"]

    def test_concept_from_dict(self):
        """Test Concept deserialization."""
        fact_id = str(uuid4())
        data = {
            "id": str(uuid4()),
            "symbolic_repr": "location.predict",
            "natural_lang_repr": "User will likely be at office tomorrow",
            "derivation_method": "ml_inference",
            "proof_chain": "Based on historical patterns",
            "payload": {},
            "confidence": 0.6,
            "source_facts": [fact_id],
            "validated": False,
            "created_at": "2026-04-30T09:00:00",
            "retracted_at": None,
        }

        concept = Concept.from_dict(data)
        assert concept.symbolic_repr == "location.predict"
        assert concept.derivation_method == "ml_inference"
        assert len(concept.source_facts) == 1

    def test_concept_is_active(self):
        """Test is_active check."""
        concept = Concept(symbolic_repr="test")
        assert concept.is_active()


class TestFactTypes:
    """Tests for FactType enum."""

    def test_fact_type_values(self):
        """Test FactType values."""
        assert FactType.USER_PREFERENCE.value == "user_preference"
        assert FactType.LOCATION.value == "location"
        assert FactType.ACTIVITY.value == "activity"
        assert FactType.CALENDAR.value == "calendar"

    def test_fact_type_from_string(self):
        """Test creating FactType from string."""
        ft = FactType("user_fact")
        assert ft == FactType.USER_FACT


class TestFactMutability:
    """Tests for FactMutability enum."""

    def test_mutability_values(self):
        """Test mutability values."""
        assert FactMutability.STATIC.value == "static"
        assert FactMutability.MUTABLE.value == "mutable"
        assert FactMutability.EPHEMERAL.value == "ephemeral"


class TestFactRepositoryInterface:
    """Tests to verify FactRepository interface."""

    def test_fact_repository_is_abc(self):
        """Test that FactRepository is abstract."""
        assert hasattr(FactRepository, "store")
        assert hasattr(FactRepository, "get")
        assert hasattr(FactRepository, "search")

    @pytest.mark.asyncio
    async def test_interface_placeholder(self):
        """Placeholder test - interface methods are abstract."""
        # The FactRepository ABC methods are all abstract,
        # so we just verify the interface exists
        assert FactRepository.store is not None


class TestConceptRepositoryInterface:
    """Tests to verify ConceptRepository interface."""

    def test_concept_repository_is_abc(self):
        """Test that ConceptRepository is abstract."""
        assert hasattr(ConceptRepository, "store")
        assert hasattr(ConceptRepository, "get")

    @pytest.mark.asyncio
    async def test_interface_placeholder(self):
        """Placeholder test - interface methods are abstract."""
        assert ConceptRepository.store is not None


class TestFactExtractorInterface:
    """Tests to verify FactExtractor interface."""

    def test_fact_extractor_is_abc(self):
        """Test that FactExtractor is abstract."""
        assert hasattr(FactExtractor, "extract_from_text")
        assert hasattr(FactExtractor, "extract_from_event")
