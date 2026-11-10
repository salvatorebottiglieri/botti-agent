"""Tests for PostgresFactRepository pgvector support (ADR-0008, issue #31).

Invariants under test:
    * A stored embedding round-trips through the text wire format that
      asyncpg must use (pgvector has no binary codec).
    * A semantic search orders by cosine distance and asks for 50 candidates
      by default; without an embedding the lexical ILIKE path is used with
      its 10-row default.
    * Backfill updates one row per (id, embedding) pair in a single session.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from cortex.memory.models import Fact, FactType
from cortex.memory.repository import PostgresFactRepository

VECTOR = [0.125, -0.25, 0.5, 0.75]
VECTOR_LITERAL = "[0.125,-0.25,0.5,0.75]"


def _row(**overrides):
    """A facts row as asyncpg returns it (embedding is pgvector text)."""
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
        "embedding": None,
    }
    row.update(overrides)
    return row


class TestPostgresFactRepositoryEmbeddings:
    """Embedding persistence and retrieval in PostgresFactRepository."""

    @pytest.fixture
    def mock_db_session(self):
        """Create a mock DbSession context manager."""
        mock = MagicMock()
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=None)
        mock.fetchrow = AsyncMock()
        mock.fetch = AsyncMock()
        mock.execute = AsyncMock()
        return mock

    @pytest.fixture
    def repo_with_mock(self, mock_db_session):
        """Create a repository with mocked DB."""
        with patch("cortex.memory.repository.DbSession") as mock_db_session_class:
            mock_db_session_class.return_value = mock_db_session
            yield PostgresFactRepository(), mock_db_session

    @pytest.mark.asyncio
    async def test_store_writes_vector_literal_and_reads_text_vector_back(self, repo_with_mock):
        """store sends the text literal into $7::vector and parses the RETURNING text."""
        repo, mock_db_session = repo_with_mock
        mock_db_session.fetchrow.return_value = _row(embedding=VECTOR_LITERAL)

        stored = await repo.store(
            Fact(
                symbolic_repr="location.home",
                natural_lang_repr="At home",
                embedding=VECTOR,
            )
        )

        sql, *params = mock_db_session.fetchrow.call_args.args
        assert "$7::vector" in sql
        assert params[6] == VECTOR_LITERAL
        assert "embedding" in sql  # RETURNING must include the new column
        assert stored.embedding == VECTOR

    @pytest.mark.asyncio
    async def test_store_without_embedding_writes_null(self, repo_with_mock):
        """A fact with no embedding binds SQL NULL, not a bogus vector literal."""
        repo, mock_db_session = repo_with_mock
        mock_db_session.fetchrow.return_value = _row(embedding=None)

        stored = await repo.store(Fact(symbolic_repr="location.home", natural_lang_repr="At home"))

        _, *params = mock_db_session.fetchrow.call_args.args
        assert params[6] is None
        assert stored.embedding is None

    @pytest.mark.asyncio
    async def test_get_parses_text_embedding(self, repo_with_mock):
        """get maps a pgvector text value back to list[float]."""
        repo, mock_db_session = repo_with_mock
        fact_id = uuid4()
        mock_db_session.fetchrow.return_value = _row(id=fact_id, embedding=VECTOR_LITERAL)

        fact = await repo.get(fact_id)

        sql = mock_db_session.fetchrow.call_args.args[0]
        assert "embedding" in sql
        assert fact is not None
        assert fact.embedding == VECTOR

    @pytest.mark.asyncio
    async def test_get_accepts_already_parsed_embedding_sequence(self, repo_with_mock):
        """A driver that returns a sequence (not text) still maps to list[float]."""
        repo, mock_db_session = repo_with_mock
        mock_db_session.fetchrow.return_value = _row(embedding=[0.5, 1.25])

        fact = await repo.get(uuid4())

        assert fact is not None
        assert fact.embedding == [0.5, 1.25]
        assert all(isinstance(value, float) for value in fact.embedding)

    @pytest.mark.asyncio
    async def test_get_parses_text_embedding_with_spaces_and_brackets(self, repo_with_mock):
        """The text parser tolerates surrounding spaces and per-component spacing."""
        repo, mock_db_session = repo_with_mock
        mock_db_session.fetchrow.return_value = _row(embedding=" [0.5, 1.25, -2.75] ")

        fact = await repo.get(uuid4())

        assert fact is not None
        assert fact.embedding == [0.5, 1.25, -2.75]

    @pytest.mark.asyncio
    async def test_update_writes_embedding_as_vector_literal(self, repo_with_mock):
        """update() accepts an embedding key and casts its literal to vector."""
        repo, mock_db_session = repo_with_mock
        fact_id = uuid4()
        mock_db_session.fetchrow.return_value = _row(id=fact_id, embedding=VECTOR_LITERAL)

        updated = await repo.update(fact_id, {"embedding": VECTOR})

        sql, *params = mock_db_session.fetchrow.call_args.args
        assert "embedding = $1::vector" in sql
        assert params == [VECTOR_LITERAL, fact_id]
        assert "embedding" in sql  # RETURNING must include the new column
        assert updated is not None
        assert updated.embedding == VECTOR

    @pytest.mark.asyncio
    async def test_update_with_none_embedding_writes_null(self, repo_with_mock):
        """An explicit None embedding is SQL NULL, not the string "None"."""
        repo, mock_db_session = repo_with_mock
        mock_db_session.fetchrow.return_value = _row(embedding=None)

        await repo.update(uuid4(), {"embedding": None})

        _, *params = mock_db_session.fetchrow.call_args.args
        assert params[0] is None


class TestPostgresFactRepositorySearch:
    """Lexical ILIKE search path."""

    @pytest.fixture
    def mock_db_session(self):
        mock = MagicMock()
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=None)
        mock.fetchrow = AsyncMock()
        mock.fetch = AsyncMock()
        mock.execute = AsyncMock()
        return mock

    @pytest.fixture
    def repo_with_mock(self, mock_db_session):
        with patch("cortex.memory.repository.DbSession") as mock_db_session_class:
            mock_db_session_class.return_value = mock_db_session
            yield PostgresFactRepository(), mock_db_session

    @pytest.mark.asyncio
    async def test_lexical_search_falls_back_to_ilike_with_default_10(self, repo_with_mock):
        """The ILIKE path runs with its 10-row default."""
        repo, mock_db_session = repo_with_mock
        mock_db_session.fetch.return_value = [_row(embedding=None)]

        results = await repo.search("home")

        sql, *params = mock_db_session.fetch.call_args.args
        assert "ILIKE" in sql
        assert "<=>" not in sql
        assert "LIMIT $2" in sql
        assert params == ["%home%", 10]
        assert len(results) == 1
        # The lexical path must still return the embedding column.
        assert "embedding" in sql

    @pytest.mark.asyncio
    async def test_lexical_search_applies_filters_with_correct_numbering(self, repo_with_mock):
        """The lexical path keeps its filters working alongside the new limit parameter."""
        repo, mock_db_session = repo_with_mock
        mock_db_session.fetch.return_value = []

        await repo.search(
            "home",
            fact_types=[FactType.LOCATION],
            min_confidence=0.7,
        )

        sql, *params = mock_db_session.fetch.call_args.args
        assert "type IN ($2)" in sql
        assert "confidence >= $3" in sql
        assert "LIMIT $4" in sql
        assert params == ["%home%", "location", 0.7, 10]

    @pytest.mark.asyncio
    async def test_lexical_search_honours_explicit_limit(self, repo_with_mock):
        """An explicit limit replaces the lexical default of 10."""
        repo, mock_db_session = repo_with_mock
        mock_db_session.fetch.return_value = []

        await repo.search("home", limit=3)

        sql, *params = mock_db_session.fetch.call_args.args
        assert "ILIKE" in sql
        assert params[-1] == 3


class TestPostgresFactRepositoryBackfill:
    """backfill_embeddings writes one UPDATE per (id, vector) pair."""

    @pytest.fixture
    def mock_db_session(self):
        mock = MagicMock()
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=None)
        mock.fetchrow = AsyncMock()
        mock.fetch = AsyncMock()
        mock.execute = AsyncMock()
        transaction = MagicMock()
        transaction.__aenter__ = AsyncMock(return_value=None)
        transaction.__aexit__ = AsyncMock(return_value=None)
        mock.transaction = MagicMock(return_value=transaction)
        return mock

    @pytest.fixture
    def repo_with_mock(self, mock_db_session):
        with patch("cortex.memory.repository.DbSession") as mock_db_session_class:
            mock_db_session_class.return_value = mock_db_session
            yield PostgresFactRepository(), mock_db_session

    @pytest.mark.asyncio
    async def test_backfill_updates_each_id_with_its_literal(self, repo_with_mock):
        """Each pair becomes UPDATE ... $2::vector inside one DB session."""
        repo, mock_db_session = repo_with_mock
        ids = [uuid4(), uuid4()]
        embeddings = [[0.1, 0.2, 0.3], [-0.4, 0.5, -0.6]]

        await repo.backfill_embeddings(ids, embeddings)

        mock_db_session.__aenter__.assert_awaited_once()
        assert mock_db_session.execute.await_count == 2
        first_sql, first_id, first_literal = mock_db_session.execute.call_args_list[0].args
        second_sql, second_id, second_literal = mock_db_session.execute.call_args_list[1].args
        assert "UPDATE facts SET embedding = $2::vector WHERE id = $1" in first_sql
        assert "UPDATE facts SET embedding = $2::vector WHERE id = $1" in second_sql
        assert (first_id, first_literal) == (ids[0], "[0.1,0.2,0.3]")
        assert (second_id, second_literal) == (ids[1], "[-0.4,0.5,-0.6]")

    @pytest.mark.asyncio
    async def test_backfill_wraps_every_update_in_one_transaction(self, repo_with_mock):
        """Witness: one autocommitted UPDATE per pair leaves partial writes
        when a later pair fails; the batch must open a transaction before the
        first UPDATE and close it after the last."""
        repo, mock_db_session = repo_with_mock
        events: list[str] = []
        mock_db_session.execute = AsyncMock(side_effect=lambda *args: events.append("update"))
        transaction = mock_db_session.transaction.return_value
        transaction.__aenter__ = AsyncMock(side_effect=lambda: events.append("begin"))
        transaction.__aexit__ = AsyncMock(side_effect=lambda *args: events.append("commit"))

        await repo.backfill_embeddings(
            [uuid4(), uuid4()], [[0.1, 0.2], [0.3, 0.4]]
        )

        assert events == ["begin", "update", "update", "commit"]
        mock_db_session.transaction.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_backfill_length_mismatch_raises_before_any_write(self, repo_with_mock):
        """Two ids and one embedding is rejected, and no UPDATE runs."""
        repo, mock_db_session = repo_with_mock

        with pytest.raises(ValueError, match="same length"):
            await repo.backfill_embeddings([uuid4(), uuid4()], [[0.1, 0.2]])

        mock_db_session.execute.assert_not_awaited()


class TestPostgresFactRepositoryMissingEmbeddings:
    """Keyset selection of null-embedding facts and the CLI session seam."""

    @pytest.fixture
    def mock_db_session(self):
        mock = MagicMock()
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=None)
        mock.fetchrow = AsyncMock()
        mock.fetch = AsyncMock()
        mock.execute = AsyncMock()
        return mock

    @pytest.fixture
    def repo_with_mock(self, mock_db_session):
        with patch("cortex.memory.repository.DbSession") as mock_db_session_class:
            mock_db_session_class.return_value = mock_db_session
            yield PostgresFactRepository(), mock_db_session

    @pytest.mark.asyncio
    async def test_list_missing_embeddings_uses_null_predicate_and_keyset(
        self, repo_with_mock
    ):
        """Witness: dropping the null predicate re-embeds rows; dropping the
        keyset re-reads page one; retracted rows must never be filled."""
        repo, mock_db_session = repo_with_mock
        first, second = uuid4(), uuid4()
        mock_db_session.fetch.return_value = [
            _row(id=first, embedding=None),
            _row(id=second, embedding=None),
        ]

        facts = await repo.list_missing_embeddings(limit=2)

        sql, *params = mock_db_session.fetch.call_args.args
        assert "embedding IS NULL" in sql
        assert "retracted_at IS NULL" in sql
        assert "id > $2::uuid" in sql
        assert "ORDER BY id" in sql
        assert "LIMIT $1" in sql
        assert params == [2, None]
        assert [fact.id for fact in facts] == [first, second]
        assert all(fact.embedding is None for fact in facts)

    @pytest.mark.asyncio
    async def test_list_missing_embeddings_passes_explicit_after_id(self, repo_with_mock):
        """A non-None cursor is bound to $2 as a UUID, not interpolated."""
        repo, mock_db_session = repo_with_mock
        after_id = uuid4()
        mock_db_session.fetch.return_value = []

        await repo.list_missing_embeddings(limit=5, after_id=after_id)

        _, *params = mock_db_session.fetch.call_args.args
        assert params == [5, after_id]

    @pytest.mark.asyncio
    async def test_session_factory_is_used_instead_of_the_pool(self, repo_with_mock):
        """Witness: the CLI passes its own connection; a repository that still
        opens DbSession ignores --db-url and hits the shared pool."""
        _, pool_session = repo_with_mock
        factory_session = MagicMock()
        factory_session.__aenter__ = AsyncMock(return_value=factory_session)
        factory_session.__aexit__ = AsyncMock(return_value=None)
        factory_session.fetch = AsyncMock(return_value=[])

        with patch("cortex.memory.repository.DbSession") as db_session_class:
            repo = PostgresFactRepository(session_factory=lambda: factory_session)
            await repo.list_missing_embeddings(limit=1)

        db_session_class.assert_not_called()
        pool_session.fetch.assert_not_awaited()
        factory_session.fetch.assert_awaited_once()
