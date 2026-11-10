"""Tests for ANN plus symbolic re-ranking of facts (ADR-0008, issue #32).

Invariants under test:
    * ``search_semantic`` fetches the top 50 ANN candidates and returns the
      top ``limit`` after symbolic re-ranking, never re-querying the DB.
    * FactType, confidence and recency all change the final order; similarity
      dominates when the symbolic metadata is equal.
    * ``ContextProvider`` embeds the query and uses the semantic path when an
      ``LLMClient`` is configured; an embedding failure, a pgvector failure or
      an empty ANN pool falls back to ILIKE and marks "facts" degraded.
    * Without an LLM client the lexical path is unchanged and not degraded.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from cortex.llm.base import LLMClient
from cortex.memory.context_provider import ContextProvider
from cortex.memory.interfaces import FactRepository
from cortex.memory.models import Fact, FactType
from cortex.memory.repository import PostgresFactRepository

VECTOR = [0.125, -0.25, 0.5, 0.75]
VECTOR_LITERAL = "[0.125,-0.25,0.5,0.75]"
QUERY = "where do I work?"


def _row(**overrides):
    """A facts row as asyncpg returns it, plus the ANN similarity column."""
    row = {
        "id": uuid4(),
        "type": "location",
        "mutability": "mutable",
        "symbolic_repr": "location.home",
        "natural_lang_repr": "At home",
        "payload": {},
        "confidence": 0.8,
        "created_at": datetime.now(UTC),
        "retracted_at": None,
        "last_accessed_at": None,
        "access_count": 0,
        "embedding": VECTOR_LITERAL,
        "similarity": 0.5,
    }
    row.update(overrides)
    return row


def _mock_db_session():
    mock = MagicMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=None)
    mock.fetchrow = AsyncMock()
    mock.fetch = AsyncMock()
    mock.execute = AsyncMock()
    return mock


def _llm_client(vector=None, error=None):
    client = MagicMock(spec=LLMClient)
    client.embed = AsyncMock(return_value=vector, side_effect=error)
    return client


class TestSemanticSearchPipeline:
    """``search_semantic`` = ANN candidate pool -> symbolic re-ranking."""

    @pytest.fixture
    def mock_db_session(self):
        return _mock_db_session()

    @pytest.fixture
    def repo_with_mock(self, mock_db_session):
        with patch("cortex.memory.repository.DbSession") as mock_db_session_class:
            mock_db_session_class.return_value = mock_db_session
            yield PostgresFactRepository(), mock_db_session

    @pytest.mark.asyncio
    async def test_ann_runs_with_50_candidates_and_returns_top_10(self, repo_with_mock):
        """50 ANN candidates, cosine similarity and ivfflat probes; 10 results out."""
        repo, mock_db_session = repo_with_mock
        rows = [_row(id=uuid4(), similarity=1 - i / 100) for i in range(50)]
        mock_db_session.fetch.return_value = rows

        results = await repo.search_semantic(VECTOR, limit=10)

        assert len(results) == 10
        sql, *params = mock_db_session.fetch.call_args.args
        assert "embedding <=> $1::vector" in sql
        assert "1 - (embedding <=> $1::vector) AS similarity" in sql
        assert "embedding IS NOT NULL" in sql
        assert "retracted_at IS NULL" in sql
        assert "LIMIT $2" in sql
        assert params == [VECTOR_LITERAL, 50]
        mock_db_session.execute.assert_awaited_once_with("SET ivfflat.probes = 10")
        mock_db_session.fetch.assert_awaited_once()
        assert [fact.id for fact in results] == [row["id"] for row in rows[:10]]

    @pytest.mark.asyncio
    async def test_fact_type_filter_drops_wrong_type_after_ann(self, repo_with_mock):
        """A PAYMENT candidate ranked above a LOCATION one is dropped; LOCATION survives."""
        repo, mock_db_session = repo_with_mock
        payment = _row(
            type="payment",
            symbolic_repr="payment.coffee",
            natural_lang_repr="Paid for coffee",
            similarity=0.95,
        )
        location = _row(
            type="location",
            symbolic_repr="location.office",
            natural_lang_repr="At the office",
            similarity=0.8,
        )
        mock_db_session.fetch.return_value = [payment, location]

        results = await repo.search_semantic(VECTOR, fact_types=[FactType.LOCATION])

        sql = mock_db_session.fetch.call_args.args[0]
        assert "type IN" not in sql  # filtering happens on the candidate pool
        assert [fact.id for fact in results] == [location["id"]]

    @pytest.mark.asyncio
    async def test_min_confidence_drops_low_confidence_candidate(self, repo_with_mock):
        """A high-similarity fact below the threshold loses to a lower-similarity one."""
        repo, mock_db_session = repo_with_mock
        low = _row(confidence=0.3, similarity=0.9)
        high = _row(confidence=0.8, similarity=0.5)
        mock_db_session.fetch.return_value = [low, high]

        results = await repo.search_semantic(VECTOR, min_confidence=0.5)

        assert [fact.id for fact in results] == [high["id"]]

    @pytest.mark.asyncio
    async def test_recent_fact_outranks_old_fact_at_equal_similarity(self, repo_with_mock):
        """1-hour-old vs 10-day-old at equal similarity/confidence: recent first.

        The old row is naive like a timestamp a driver may return; it must be
        treated as UTC, not crash against the aware recent row.
        """
        repo, mock_db_session = repo_with_mock
        now = datetime.now(UTC)
        recent = _row(created_at=now - timedelta(hours=1), similarity=0.7, confidence=0.6)
        old = _row(created_at=now - timedelta(days=10), similarity=0.7, confidence=0.6)
        old["created_at"] = old["created_at"].replace(tzinfo=None)
        mock_db_session.fetch.return_value = [old, recent]

        results = await repo.search_semantic(VECTOR)

        assert [fact.id for fact in results] == [recent["id"], old["id"]]

    @pytest.mark.asyncio
    async def test_high_confidence_fact_outranks_low_confidence_at_equal_similarity(
        self, repo_with_mock
    ):
        """0.9 vs 0.3 confidence, same similarity and recency: 0.9 first."""
        repo, mock_db_session = repo_with_mock
        created_at = datetime.now(UTC) - timedelta(hours=1)
        low = _row(confidence=0.3, similarity=0.5, created_at=created_at)
        high = _row(confidence=0.9, similarity=0.5, created_at=created_at)
        mock_db_session.fetch.return_value = [low, high]

        results = await repo.search_semantic(VECTOR)

        assert [fact.id for fact in results] == [high["id"], low["id"]]

    @pytest.mark.asyncio
    async def test_equal_scores_tie_break_on_newer_created_at(self, repo_with_mock):
        """Two facts past the recency window get equal scores; the newer one wins."""
        repo, mock_db_session = repo_with_mock
        now = datetime.now(UTC)
        older = _row(created_at=now - timedelta(days=30), similarity=0.7, confidence=0.6)
        newer = _row(created_at=now - timedelta(days=10), similarity=0.7, confidence=0.6)
        mock_db_session.fetch.return_value = [older, newer]

        results = await repo.search_semantic(VECTOR)

        assert [fact.id for fact in results] == [newer["id"], older["id"]]

    @pytest.mark.asyncio
    async def test_similarity_dominates_when_symbolic_metadata_is_equal(self, repo_with_mock):
        """0.9 vs 0.4 similarity with identical type/confidence/recency: 0.9 first."""
        repo, mock_db_session = repo_with_mock
        created_at = datetime.now(UTC) - timedelta(hours=1)
        weak = _row(similarity=0.4, confidence=0.8, created_at=created_at)
        strong = _row(similarity=0.9, confidence=0.8, created_at=created_at)
        mock_db_session.fetch.return_value = [weak, strong]

        results = await repo.search_semantic(VECTOR)

        assert [fact.id for fact in results] == [strong["id"], weak["id"]]


class TestContextProviderSemanticPath:
    """``ContextProvider`` chooses semantic retrieval with a fallback to ILIKE."""

    @pytest.fixture
    def fact_repo(self):
        repo = MagicMock(spec=FactRepository)
        repo.search = AsyncMock(return_value=[])
        repo.search_semantic = AsyncMock(return_value=[])
        repo.get_by_type = AsyncMock(return_value=[])
        return repo

    def _provider(self, fact_repo, llm_client):
        return ContextProvider(
            fact_repository=fact_repo,
            fact_store=MagicMock(),
            llm_client=llm_client,
        )

    @pytest.mark.asyncio
    async def test_semantic_path_embeds_query_and_skips_lexical(self, fact_repo):
        """The query is embedded, search_semantic gets the vector and 0.4 default."""
        semantic_fact = Fact(
            type=FactType.LOCATION,
            symbolic_repr="location.office",
            natural_lang_repr="User works at the office",
            confidence=0.9,
        )
        fact_repo.search_semantic.return_value = [semantic_fact]
        client = _llm_client(VECTOR)

        bundle = await self._provider(fact_repo, client).get_memory_context(
            None, QUERY, max_facts=5
        )

        client.embed.assert_awaited_once_with(QUERY)
        fact_repo.search_semantic.assert_awaited_once_with(
            VECTOR, limit=5, fact_types=None, min_confidence=0.4
        )
        fact_repo.search.assert_not_awaited()
        assert bundle.facts == [semantic_fact]
        assert "facts" not in bundle.degraded_dimensions

    @pytest.mark.asyncio
    async def test_semantic_path_forwards_filters(self, fact_repo):
        """fact_types reaches search_semantic and the 0.4 default applies."""
        fact_repo.search_semantic.return_value = [Fact(symbolic_repr="location.office")]
        client = _llm_client(VECTOR)

        await self._provider(fact_repo, client).get_memory_context(
            None,
            QUERY,
            fact_types=[FactType.LOCATION],
            max_facts=3,
        )

        fact_repo.search_semantic.assert_awaited_once_with(
            VECTOR, limit=3, fact_types=[FactType.LOCATION], min_confidence=0.4
        )

    @pytest.mark.asyncio
    async def test_embedding_failure_falls_back_to_lexical_and_degrades_facts(self, fact_repo):
        """An embedding provider error must not hide retrievable facts."""
        lexical_fact = Fact(symbolic_repr="fact.legacy", natural_lang_repr="Legacy fact")
        fact_repo.search.return_value = [lexical_fact]
        client = _llm_client(error=RuntimeError("embedding endpoint down"))

        bundle = await self._provider(fact_repo, client).get_memory_context(
            None, QUERY, max_facts=5
        )

        client.embed.assert_awaited_once_with(QUERY)
        fact_repo.search_semantic.assert_not_awaited()
        fact_repo.search.assert_awaited_once_with(
            QUERY, limit=10, fact_types=None, min_confidence=0.4
        )
        assert bundle.facts == [lexical_fact]
        assert bundle.degraded_dimensions == ["facts"]

    @pytest.mark.asyncio
    async def test_pgvector_failure_falls_back_to_lexical_and_degrades_facts(self, fact_repo):
        """search_semantic raising (no pgvector) still returns ILIKE facts, once degraded."""
        lexical_fact = Fact(symbolic_repr="fact.legacy", natural_lang_repr="Legacy fact")
        fact_repo.search_semantic.side_effect = RuntimeError("pgvector unavailable")
        fact_repo.search.return_value = [lexical_fact]
        client = _llm_client(VECTOR)

        bundle = await self._provider(fact_repo, client).get_memory_context(
            None, QUERY, max_facts=5
        )

        client.embed.assert_awaited_once_with(QUERY)
        fact_repo.search_semantic.assert_awaited_once()
        fact_repo.search.assert_awaited_once_with(
            QUERY, limit=10, fact_types=None, min_confidence=0.4
        )
        assert bundle.facts == [lexical_fact]
        assert bundle.degraded_dimensions == ["facts"]

    @pytest.mark.asyncio
    async def test_empty_semantic_pool_falls_back_to_lexical_and_degrades_facts(self, fact_repo):
        """Facts stored without embeddings must stay retrievable."""
        lexical_fact = Fact(symbolic_repr="fact.legacy", natural_lang_repr="Legacy fact")
        fact_repo.search_semantic.return_value = []
        fact_repo.search.return_value = [lexical_fact]
        client = _llm_client(VECTOR)

        bundle = await self._provider(fact_repo, client).get_memory_context(
            None, QUERY, max_facts=5
        )

        fact_repo.search_semantic.assert_awaited_once()
        fact_repo.search.assert_awaited_once_with(
            QUERY, limit=10, fact_types=None, min_confidence=0.4
        )
        assert bundle.facts == [lexical_fact]
        assert bundle.degraded_dimensions == ["facts"]

    @pytest.mark.asyncio
    async def test_without_llm_client_uses_lexical_path_without_degradation(self, fact_repo):
        """Backward compatibility: no client means the old path and no degraded flag."""
        lexical_fact = Fact(symbolic_repr="fact.plain", natural_lang_repr="A plain fact")
        fact_repo.search.return_value = [lexical_fact]

        bundle = await self._provider(fact_repo, None).get_memory_context(
            None, QUERY, max_facts=5
        )

        fact_repo.search_semantic.assert_not_awaited()
        fact_repo.search.assert_awaited_once_with(
            QUERY, limit=10, fact_types=None, min_confidence=0.4
        )
        assert bundle.facts == [lexical_fact]
        assert bundle.degraded_dimensions == []

    @pytest.mark.asyncio
    async def test_get_relevant_facts_stays_lexical_even_with_llm_client(self, fact_repo):
        """The public query method keeps its existing contract for callers."""
        lexical_fact = Fact(symbolic_repr="location.work", natural_lang_repr="At work")
        fact_repo.search.return_value = [lexical_fact]
        client = _llm_client(VECTOR)

        result = await self._provider(fact_repo, client).get_relevant_facts(
            session_id=None, query="work", limit=5
        )

        client.embed.assert_not_awaited()
        fact_repo.search_semantic.assert_not_awaited()
        fact_repo.search.assert_awaited_once_with(
            "work", limit=10, fact_types=None, min_confidence=0.4
        )
        assert result == [lexical_fact]
