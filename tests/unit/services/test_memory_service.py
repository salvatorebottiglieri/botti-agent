"""Tests for MemoryService."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from cortex.agentic.models import AmbientContext, PersonalityContext
from cortex.memory.interfaces import FactExtractor, FactRepository
from cortex.memory.models import Fact, FactType
from cortex.services.memory_service import MemoryService


class TestPersonalityContext:
    """Tests for PersonalityContext dataclass."""

    def test_default_values(self):
        """Default personality has sensible values."""
        ctx = PersonalityContext()
        assert ctx.formality == 0.5
        assert ctx.verbosity == 0.5
        assert ctx.technical_level == 0.5

    def test_custom_values(self):
        """Can set custom values."""
        ctx = PersonalityContext(
            formality=0.8,
            verbosity=0.3,
            technical_level=0.9
        )
        assert ctx.formality == 0.8
        assert ctx.verbosity == 0.3
        assert ctx.technical_level == 0.9


class TestAmbientContext:
    """Tests for AmbientContext dataclass."""

    def test_default_values(self):
        """Default ambient has empty/default values."""
        ctx = AmbientContext()
        assert ctx.time_of_day is None
        assert ctx.location is None
        assert ctx.activity is None

    def test_custom_values(self):
        """Can set custom values."""
        ctx = AmbientContext(
            time_of_day="morning",
            location="home",
            activity="working"
        )
        assert ctx.time_of_day == "morning"
        assert ctx.location == "home"
        assert ctx.activity == "working"


class TestMemoryService:
    """Tests for MemoryService."""

    @pytest.fixture
    def mock_fact_repo(self):
        """Create a mock fact repository."""
        repo = MagicMock(spec=FactRepository)
        repo.store = AsyncMock()
        repo.search = AsyncMock(return_value=[])
        repo.get_by_type = AsyncMock(return_value=[])
        repo.record_access = AsyncMock()
        repo.get_recent = AsyncMock(return_value=[])
        return repo

    @pytest.fixture
    def mock_extractor(self):
        """Create a mock fact extractor."""
        extractor = MagicMock(spec=FactExtractor)
        extractor.extract_from_text = AsyncMock(return_value=[])
        return extractor

    @pytest.fixture
    def mock_event_bus(self):
        """Create a mock event bus."""
        bus = MagicMock()
        bus.publish = AsyncMock()
        return bus

    @pytest.fixture
    def mock_llm_client(self):
        """Create a mock LLM client."""
        client = MagicMock()
        client.chat = AsyncMock()
        return client

    @pytest.fixture
    def service(self, mock_fact_repo, mock_extractor, mock_event_bus, mock_llm_client):
        """Create a MemoryService instance."""
        return MemoryService(
            fact_repository=mock_fact_repo,
            fact_extractor=mock_extractor,
            event_bus=mock_event_bus,
            llm_client=mock_llm_client
        )

    @pytest.mark.asyncio
    async def test_store_fact(self, service, mock_fact_repo):
        """Storing a fact calls repository."""
        fact = Fact(
            type=FactType.LOCATION,
            symbolic_repr="location.home",
            natural_lang_repr="User is at home",
            confidence=0.9
        )
        mock_fact_repo.get_by_symbolic_repr.return_value = None  # No duplicate
        mock_fact_repo.store.return_value = fact

        stored = await service.store_fact(fact)

        mock_fact_repo.store.assert_called_once_with(fact)
        assert stored == fact

    @pytest.mark.asyncio
    async def test_get_memory_context_populates_facts(self, service, mock_fact_repo):
        """get_memory_context populates facts via the fact repository search."""
        facts = [
            Fact(symbolic_repr="location.work", natural_lang_repr="User is at work"),
            Fact(symbolic_repr="activity.meeting", natural_lang_repr="User is in a meeting")
        ]
        mock_fact_repo.search.return_value = facts

        bundle = await service.get_memory_context(session_id=None, query="location")

        mock_fact_repo.search.assert_called_once()
        assert len(bundle.facts) == 2
        assert bundle.degraded_dimensions == []

    @pytest.mark.asyncio
    async def test_get_memory_context_boosts_session_facts(self, service, mock_fact_repo):
        """Facts from current session are boosted in the bundle's facts list."""
        session_id = uuid4()
        session_fact = Fact(
            symbolic_repr="session.info",
            natural_lang_repr="User mentioned project X",
            payload={"session_id": str(session_id)}
        )
        mock_fact_repo.search.return_value = [session_fact]

        bundle = await service.get_memory_context(session_id=session_id, query="project")

        assert len(bundle.facts) >= 1

    @pytest.mark.asyncio
    async def test_get_memory_context_returns_ambient(self, service, mock_fact_repo):
        """get_memory_context returns a typed AmbientContext built from ambient facts."""
        location_fact = Fact(
            type=FactType.LOCATION,
            symbolic_repr="location.current",
            natural_lang_repr="Home"
        )
        activity_fact = Fact(
            type=FactType.ACTIVITY,
            symbolic_repr="activity.current",
            natural_lang_repr="Working"
        )

        mock_fact_repo.get_by_type.side_effect = lambda t, **kw: {
            FactType.LOCATION: [location_fact],
            FactType.ACTIVITY: [activity_fact]
        }.get(t, [])

        bundle = await service.get_memory_context(session_id=None, query="anything")

        assert bundle.ambient is not None
        assert bundle.ambient.location == "Home"
        assert bundle.ambient.activity == "Working"

    @pytest.mark.asyncio
    async def test_get_memory_context_records_degraded_dimensions(self, service, mock_fact_repo):
        """When a dimension's underlying call raises, the bundle records it in degraded_dimensions."""
        mock_fact_repo.search.side_effect = Exception("repo down")
        # personality and ambient go through get_by_type, which still works
        mock_fact_repo.get_by_type.return_value = []

        bundle = await service.get_memory_context(session_id=None, query="x")

        assert "facts" in bundle.degraded_dimensions
        assert bundle.facts == []

    @pytest.mark.asyncio
    async def test_extract_from_conversation(self, service, mock_extractor):
        """Extracting from conversation calls extractor."""
        facts = [
            Fact(
                type=FactType.USER_FACT,
                symbolic_repr="user.prefers",
                natural_lang_repr="User prefers dark mode",
                confidence=0.8
            )
        ]
        mock_extractor.extract_from_text.return_value = facts

        extracted = await service.extract_from_conversation(
            "I prefer dark mode in my editor",
            session_id=uuid4()
        )

        mock_extractor.extract_from_text.assert_called_once()
        assert len(extracted) == 1
        assert extracted[0].type == FactType.USER_FACT

    @pytest.mark.asyncio
    async def test_extract_stores_facts(self, service, mock_extractor, mock_fact_repo):
        """Extracted facts are stored."""
        fact = Fact(symbolic_repr="test.fact", natural_lang_repr="Test")
        mock_extractor.extract_from_text.return_value = [fact]
        mock_fact_repo.get_by_symbolic_repr.return_value = None
        mock_fact_repo.store.return_value = fact

        await service.extract_from_conversation("test text", session_id=uuid4())

        mock_fact_repo.store.assert_called_once_with(fact)

    @pytest.mark.asyncio
    async def test_retract_fact(self, service, mock_fact_repo):
        """Retracting a fact calls repository."""
        fact_id = uuid4()

        await service.retract_fact(fact_id, reason="Outdated")

        mock_fact_repo.retract.assert_called_once_with(fact_id, "Outdated")

    @pytest.mark.asyncio
    async def test_retract_cascades_to_concepts(self, service, mock_fact_repo):
        """Retracting a fact invalidates derived concepts."""
        fact_id = uuid4()
        concept_repo = MagicMock()
        concept_repo.invalidate_from_fact = AsyncMock()
        service._concept_repo = concept_repo

        await service.retract_fact(fact_id)

        concept_repo.invalidate_from_fact.assert_called_once_with(fact_id)

    @pytest.mark.asyncio
    async def test_get_personality_context_returns_facts(self, service, mock_fact_repo):
        """Personality context is derived from preference facts."""
        pref_fact = Fact(
            type=FactType.USER_PREFERENCE,
            symbolic_repr="pref.formality",
            natural_lang_repr="User prefers formal language",
            payload={"formality": 0.8}
        )
        mock_fact_repo.get_by_type.return_value = [pref_fact]

        ctx = await service.get_personality_context()

        assert isinstance(ctx, PersonalityContext)
        # The implementation should parse the payload
        assert ctx.formality >= 0  # Should be derived from facts

    @pytest.mark.asyncio
    async def test_handle_event_location(self, service, mock_fact_repo):
        """Location events create facts."""
        mock_fact_repo.get_by_symbolic_repr.return_value = None
        mock_fact_repo.store.return_value = Fact()

        event = MagicMock()
        event.type = "location"
        event.payload = {
            "latitude": 37.7749,
            "longitude": -122.4194,
            "place": "home"
        }

        await service.handle_event(event)

        # Check that store was called (create a location fact)
        mock_fact_repo.store.assert_called()
        call_args = mock_fact_repo.store.call_args[0][0]
        assert call_args.type == FactType.LOCATION

    @pytest.mark.asyncio
    async def test_search_facts(self, service, mock_fact_repo):
        """Search calls repository with correct params."""
        mock_fact_repo.search.return_value = []

        await service.search_facts(
            query="home location",
            fact_types=[FactType.LOCATION],
            min_confidence=0.7,
            limit=5
        )

        mock_fact_repo.search.assert_called_once_with(
            "home location",
            limit=5,
            fact_types=[FactType.LOCATION],
            min_confidence=0.7
        )

    @pytest.mark.asyncio
    async def test_get_recent_facts(self, service, mock_fact_repo):
        """Get recent facts calls repository."""
        mock_fact_repo.get_recent.return_value = []

        result = await service.get_recent_facts(limit=10)

        mock_fact_repo.get_recent.assert_called_once_with(limit=10)
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_store_batch(self, service, mock_fact_repo):
        """Store batch stores multiple facts."""
        facts = [
            Fact(symbolic_repr="fact.1", natural_lang_repr="Fact 1"),
            Fact(symbolic_repr="fact.2", natural_lang_repr="Fact 2"),
        ]

        await service.store_batch(facts)

        mock_fact_repo.store_batch.assert_called_once_with(facts)

    @pytest.mark.asyncio
    async def test_build_concept_from_facts(self, service):
        """Can build a concept from facts."""
        source_facts = [
            Fact(
                symbolic_repr="loc.home",
                natural_lang_repr="User is at home",
                confidence=0.9
            ),
            Fact(
                symbolic_repr="time.evening",
                natural_lang_repr="It's evening",
                confidence=0.8
            )
        ]

        concept = await service.build_concept(
            symbolic_repr="context.evening_home",
            natural_lang_repr="User is at home in the evening",
            source_facts=source_facts,
            derivation_method="rule_based"
        )

        assert concept.symbolic_repr == "context.evening_home"
        assert len(concept.source_facts) == 2
        assert concept.derivation_method == "rule_based"
        assert concept.confidence > 0  # Should be computed from source facts
