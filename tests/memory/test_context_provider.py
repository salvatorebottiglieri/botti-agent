"""Tests for ContextProvider."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from cortex.agentic.models import AmbientContext, MemoryContext, PersonalityContext
from cortex.memory.context_provider import ContextProvider
from cortex.memory.interfaces import ConceptRepository, FactRepository
from cortex.memory.models import Concept, Fact, FactType


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


class TestContextProvider:
    """Tests for ContextProvider."""

    @pytest.fixture
    def mock_fact_repo(self):
        """Create a mock fact repository."""
        repo = MagicMock(spec=FactRepository)
        repo.search = AsyncMock(return_value=[])
        repo.get_by_type = AsyncMock(return_value=[])
        return repo

    @pytest.fixture
    def mock_fact_store(self):
        """Create a mock fact store."""
        store = MagicMock()
        store.retract_fact = AsyncMock(return_value=False)
        return store

    @pytest.fixture
    def mock_concept_repo(self):
        """Create a mock concept repository."""
        repo = MagicMock(spec=ConceptRepository)
        repo.invalidate_from_fact = AsyncMock()
        repo.store = AsyncMock()
        return repo

    @pytest.fixture
    def provider(self, mock_fact_repo, mock_fact_store, mock_concept_repo):
        """Create a ContextProvider instance."""
        return ContextProvider(
            fact_repository=mock_fact_repo,
            fact_store=mock_fact_store,
            concept_repository=mock_concept_repo,
        )

    @pytest.mark.asyncio
    async def test_get_memory_context_bundles_dimensions(self, provider, mock_fact_repo):
        """get_memory_context bundles facts, personality, and ambient into one MemoryContext."""
        fact = Fact(
            type=FactType.LOCATION,
            symbolic_repr="location.home",
            natural_lang_repr="At home",
            confidence=0.9,
        )
        mock_fact_repo.search.return_value = [fact]
        pref_fact = Fact(
            type=FactType.USER_PREFERENCE,
            symbolic_repr="pref.formality",
            natural_lang_repr="Prefers formal language",
            payload={"formality": 0.8},
        )
        time_fact = Fact(
            type=FactType.TIME,
            symbolic_repr="time.morning",
            natural_lang_repr="morning",
        )

        def get_by_type(fact_type, **kwargs):
            if fact_type == FactType.USER_PREFERENCE:
                return [pref_fact]
            if fact_type == FactType.TIME:
                return [time_fact]
            return []

        mock_fact_repo.get_by_type.side_effect = get_by_type

        bundle = await provider.get_memory_context(session_id=None, query="home")

        assert isinstance(bundle, MemoryContext)
        assert bundle.facts == [fact]
        assert bundle.personality is not None
        assert bundle.personality.formality == 0.8
        assert bundle.ambient is not None
        assert bundle.ambient.time_of_day == "morning"
        assert bundle.degraded_dimensions == []

    @pytest.mark.asyncio
    async def test_get_memory_context_records_degraded_dimensions(self, provider, mock_fact_repo):
        """When a dimension's underlying call raises, it is recorded in degraded_dimensions."""
        mock_fact_repo.search.side_effect = Exception("repo down")
        # personality and ambient go through get_by_type, which still works
        mock_fact_repo.get_by_type.return_value = []

        bundle = await provider.get_memory_context(session_id=None, query="x")

        assert "facts" in bundle.degraded_dimensions
        assert bundle.facts == []

    @pytest.mark.asyncio
    async def test_get_relevant_facts_calls_repo_search(self, provider, mock_fact_repo):
        """get_relevant_facts searches the repository and returns ranked facts."""
        fact = Fact(symbolic_repr="location.work", natural_lang_repr="User is at work")
        mock_fact_repo.search.return_value = [fact]

        result = await provider.get_relevant_facts(
            session_id=None, query="work", limit=5
        )

        mock_fact_repo.search.assert_awaited_once_with(
            "work",
            limit=10,
            fact_types=None,
            min_confidence=0.4,
        )
        assert result == [fact]

    @pytest.mark.asyncio
    async def test_get_relevant_facts_boosts_session_facts(self, provider, mock_fact_repo):
        """Facts from the current session rank above otherwise-equal facts."""
        session_id = uuid4()
        other = Fact(
            symbolic_repr="fact.a", natural_lang_repr="Alpha fact", confidence=0.5
        )
        session_fact = Fact(
            symbolic_repr="fact.b",
            natural_lang_repr="Beta fact",
            confidence=0.5,
            payload={"session_id": str(session_id)},
        )
        mock_fact_repo.search.return_value = [other, session_fact]

        facts = await provider.get_relevant_facts(session_id=session_id, query="zzz")

        assert facts[0] == session_fact
        assert facts[1] == other

    @pytest.mark.asyncio
    async def test_get_relevant_facts_boosts_query_in_repr(self, provider, mock_fact_repo):
        """Facts whose representations contain the query rank above otherwise-equal facts."""
        plain = Fact(
            symbolic_repr="fact.plain", natural_lang_repr="Unrelated note", confidence=0.5
        )
        matched = Fact(
            symbolic_repr="project.alpha",
            natural_lang_repr="Alpha project status",
            confidence=0.5,
        )
        mock_fact_repo.search.return_value = [plain, matched]

        facts = await provider.get_relevant_facts(session_id=None, query="project")

        assert facts[0] == matched
        assert facts[1] == plain

    @pytest.mark.asyncio
    async def test_get_relevant_facts_forwards_filters(self, provider, mock_fact_repo):
        """get_relevant_facts forwards fact_types and min_confidence to repo.search."""
        mock_fact_repo.search.return_value = []

        await provider.get_relevant_facts(
            session_id=None,
            query="home",
            fact_types=[FactType.LOCATION],
            min_confidence=0.7,
        )

        mock_fact_repo.search.assert_awaited_once_with(
            "home",
            limit=20,
            fact_types=[FactType.LOCATION],
            min_confidence=0.7,
        )

    @pytest.mark.asyncio
    async def test_get_personality_context_aggregates_preferences(self, provider, mock_fact_repo):
        """Personality context is derived from USER_PREFERENCE payload averages."""
        mock_fact_repo.get_by_type.return_value = [
            Fact(
                type=FactType.USER_PREFERENCE,
                symbolic_repr="pref.formality",
                natural_lang_repr="Prefers formal language",
                payload={"formality": 0.8, "verbosity": 0.6},
            ),
            Fact(
                type=FactType.USER_PREFERENCE,
                symbolic_repr="pref.formality.2",
                natural_lang_repr="Second preference",
                payload={"formality": 0.4},
            ),
        ]

        ctx = await provider.get_personality_context()

        mock_fact_repo.get_by_type.assert_awaited_once_with(
            FactType.USER_PREFERENCE, limit=20
        )
        assert isinstance(ctx, PersonalityContext)
        assert ctx.formality == pytest.approx(0.6)  # (0.8 + 0.4) / 2
        assert ctx.verbosity == pytest.approx(0.6)
        assert ctx.technical_level == 0.5  # Default

    @pytest.mark.asyncio
    async def test_get_ambient_context_builds_from_facts(self, provider, mock_fact_repo):
        """Ambient context is built from TIME/LOCATION/ACTIVITY/WEATHER facts."""
        def get_by_type(fact_type, **kwargs):
            return {
                FactType.TIME: [Fact(type=FactType.TIME, natural_lang_repr="morning")],
                FactType.LOCATION: [Fact(type=FactType.LOCATION, natural_lang_repr="home")],
                FactType.ACTIVITY: [Fact(type=FactType.ACTIVITY, natural_lang_repr="working")],
                FactType.WEATHER: [Fact(type=FactType.WEATHER, natural_lang_repr="sunny")],
            }.get(fact_type, [])

        mock_fact_repo.get_by_type.side_effect = get_by_type

        ambient = await provider.get_ambient_context()

        assert isinstance(ambient, AmbientContext)
        assert ambient.time_of_day == "morning"
        assert ambient.location == "home"
        assert ambient.activity == "working"
        assert ambient.weather == "sunny"

    @pytest.mark.asyncio
    async def test_get_ambient_context_none_without_facts(self, provider, mock_fact_repo):
        """Ambient context is None when no ambient facts exist."""
        mock_fact_repo.get_by_type.return_value = []

        ambient = await provider.get_ambient_context()

        assert ambient is None

    @pytest.mark.asyncio
    async def test_retract_fact_no_cascade_when_not_retracted(
        self, provider, mock_fact_store, mock_concept_repo
    ):
        """retract_fact returns False and does not cascade when FactStore reports no retraction."""
        fact_id = uuid4()
        mock_fact_store.retract_fact = AsyncMock(return_value=False)

        result = await provider.retract_fact(fact_id, reason="Outdated")

        mock_fact_store.retract_fact.assert_awaited_once_with(fact_id, "Outdated")
        mock_concept_repo.invalidate_from_fact.assert_not_called()
        assert result is False

    @pytest.mark.asyncio
    async def test_retract_fact_cascades_to_concepts(
        self, provider, mock_fact_store, mock_concept_repo
    ):
        """retract_fact returns True and invalidates concepts when the fact was retracted."""
        fact_id = uuid4()
        mock_fact_store.retract_fact = AsyncMock(return_value=True)

        result = await provider.retract_fact(fact_id)

        mock_fact_store.retract_fact.assert_awaited_once_with(fact_id, None)
        mock_concept_repo.invalidate_from_fact.assert_awaited_once_with(fact_id)
        assert result is True

    @pytest.mark.asyncio
    async def test_build_concept_stores_with_weighted_confidence(
        self, provider, mock_concept_repo
    ):
        """build_concept stores a concept with weighted confidence and a proof chain."""
        source_facts = [
            Fact(
                symbolic_repr="loc.home",
                natural_lang_repr="User is at home",
                confidence=0.9,
            ),
            Fact(
                symbolic_repr="time.evening",
                natural_lang_repr="It's evening",
                confidence=0.8,
            ),
        ]
        stored = Concept(
            symbolic_repr="context.evening_home",
            natural_lang_repr="User is at home in the evening",
            derivation_method="rule_based",
            source_facts=[f.id for f in source_facts],
            confidence=0.85,
        )
        mock_concept_repo.store.return_value = stored

        concept = await provider.build_concept(
            symbolic_repr="context.evening_home",
            natural_lang_repr="User is at home in the evening",
            source_facts=source_facts,
            derivation_method="rule_based",
        )

        mock_concept_repo.store.assert_awaited_once()
        assert concept == stored
        stored_concept = mock_concept_repo.store.call_args.args[0]
        assert stored_concept.confidence == pytest.approx(0.85)  # (0.9 + 0.8) / 2
        assert "Based on the following observations" in stored_concept.proof_chain
        assert stored_concept.validated is False

    @pytest.mark.asyncio
    async def test_build_concept_without_concept_repo(self, provider, mock_concept_repo):
        """build_concept returns the concept without storing when no concept repo is set."""
        source_facts = [
            Fact(symbolic_repr="loc.home", natural_lang_repr="At home", confidence=0.9),
        ]
        provider._concept_repo = None

        concept = await provider.build_concept(
            symbolic_repr="context.home",
            natural_lang_repr="At home",
            source_facts=source_facts,
        )

        assert concept.symbolic_repr == "context.home"
        assert concept.confidence == pytest.approx(0.9)
        assert len(concept.source_facts) == 1
        mock_concept_repo.store.assert_not_called()
