"""Tests for embedding storage wiring (ADR-0008, issue #31).

Invariants under test:
    * Migration 009 installs pgvector, the vector(1536) column and the two
      indexes; the compose stack uses an image that ships the extension.
    * Fact serializes its embedding.
    * FactStore embeds eagerly on write using natural language first, falls
      back to the symbolic form, and never fails fact storage when the
      embedding provider is down.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from cortex.memory.fact_store import FactStore
from cortex.memory.interfaces import FactRepository
from cortex.memory.models import Fact

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = ROOT / "migrations" / "009_embeddings.sql"
COMPOSE_PATH = ROOT / "docker-compose.yml"

VECTOR = [0.11, -0.22, 0.33]


class FakeEmbeddingClient:
    """Minimal LLMClient stand-in recording the texts it embeds."""

    def __init__(self, vector=None, error=None):
        self.vector = vector
        self.error = error
        self.texts: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.texts.append(text)
        if self.error is not None:
            raise self.error
        assert self.vector is not None
        return list(self.vector)


class TestEmbeddingMigration:
    """The migration and the compose image must actually provide pgvector."""

    @pytest.mark.parametrize(
        "snippet",
        [
            "CREATE EXTENSION IF NOT EXISTS vector",
            "ALTER TABLE facts ADD COLUMN IF NOT EXISTS embedding vector(1536)",
            "USING ivfflat (embedding vector_cosine_ops)",
            "WITH (lists = 100)",
            "CREATE INDEX IF NOT EXISTS idx_facts_embedding_null",
            "WHERE embedding IS NULL",
        ],
    )
    def test_migration_contains_pgvector_schema(self, snippet):
        assert snippet in MIGRATION_PATH.read_text()

    def test_compose_postgres_image_ships_pgvector(self):
        compose = COMPOSE_PATH.read_text()
        assert "image: pgvector/pgvector:pg16" in compose
        assert "image: postgres:16-alpine" not in compose


class TestFactEmbeddingSerialization:
    """Fact.embedding survives to_dict/from_dict and defaults to None."""

    def test_embedding_roundtrips_through_dict(self):
        fact = Fact(
            symbolic_repr="location.home",
            natural_lang_repr="At home",
            embedding=VECTOR,
        )

        data = fact.to_dict()

        assert data.get("embedding") == VECTOR
        restored = Fact.from_dict(data)
        assert restored.embedding == VECTOR

    def test_fact_without_embedding_roundtrips_as_none(self):
        fact = Fact(symbolic_repr="location.home")

        data = fact.to_dict()

        assert "embedding" in data
        assert data["embedding"] is None
        assert Fact.from_dict(data).embedding is None


class TestFactStoreEagerEmbedding:
    """FactStore embeds at write time when an LLM client is configured."""

    @pytest.fixture
    def repo(self):
        repo = MagicMock(spec=FactRepository)
        repo.get_by_symbolic_repr = AsyncMock(return_value=None)
        return repo

    @pytest.mark.asyncio
    async def test_new_fact_embeds_natural_language_and_stores_vector(self, repo):
        """The embedded text is the natural-language repr, not the symbolic one."""
        client = FakeEmbeddingClient(VECTOR)
        fact = Fact(symbolic_repr="location.home", natural_lang_repr="At home")
        repo.store.return_value = fact

        result = await FactStore(repo, llm_client=client).add_fact(fact)

        assert client.texts == ["At home"]
        assert fact.embedding == VECTOR
        repo.store.assert_awaited_once_with(fact)
        assert result is fact

    @pytest.mark.asyncio
    async def test_new_fact_falls_back_to_symbolic_when_natural_is_empty(self, repo):
        """An empty natural-language repr embeds the symbolic repr instead."""
        client = FakeEmbeddingClient([0.5, 0.25])
        fact = Fact(symbolic_repr="location.office", natural_lang_repr="")
        repo.store.return_value = fact

        await FactStore(repo, llm_client=client).add_fact(fact)

        assert client.texts == ["location.office"]
        assert fact.embedding == [0.5, 0.25]
        repo.store.assert_awaited_once_with(fact)

    @pytest.mark.asyncio
    async def test_provider_failure_stores_without_embedding_and_does_not_raise(
        self, repo, caplog
    ):
        """Embedding outages must not fail fact storage; the fact lands without a vector."""
        client = FakeEmbeddingClient(error=RuntimeError("embedding endpoint down"))
        fact = Fact(symbolic_repr="location.home", natural_lang_repr="At home")
        repo.store.return_value = fact

        with caplog.at_level("WARNING", logger="cortex.memory.fact_store"):
            result = await FactStore(repo, llm_client=client).add_fact(fact)

        assert result is fact
        assert fact.embedding is None
        repo.store.assert_awaited_once_with(fact)
        assert "embedding" in caplog.text.lower()

    @pytest.mark.asyncio
    async def test_mutable_duplicate_update_includes_new_embedding(self, repo):
        """A mutable duplicate recomputes the embedding and updates it in place."""
        existing = Fact(id=uuid4(), symbolic_repr="location.home")
        updated = Fact(id=existing.id, symbolic_repr="location.home", embedding=[0.4, 0.6])
        repo.get_by_symbolic_repr.return_value = existing
        repo.update.return_value = updated
        client = FakeEmbeddingClient([0.4, 0.6])

        result = await FactStore(repo, llm_client=client).add_fact(
            Fact(
                symbolic_repr="location.home",
                natural_lang_repr="At work",
                payload={"place": "work"},
                confidence=0.9,
            )
        )

        assert client.texts == ["At work"]
        repo.update.assert_awaited_once_with(
            existing.id,
            {
                "natural_lang_repr": "At work",
                "payload": {"place": "work"},
                "confidence": 0.9,
                "embedding": [0.4, 0.6],
            },
        )
        repo.store.assert_not_awaited()
        assert result is updated

    @pytest.mark.asyncio
    async def test_mutable_duplicate_embedding_failure_does_not_erase_existing(self, repo):
        """A failed re-embed leaves the existing vector untouched (no embedding key)."""
        existing = Fact(id=uuid4(), symbolic_repr="location.home", embedding=[0.9, 0.1])
        updated = Fact(id=existing.id, symbolic_repr="location.home", embedding=[0.9, 0.1])
        repo.get_by_symbolic_repr.return_value = existing
        repo.update.return_value = updated
        client = FakeEmbeddingClient(error=RuntimeError("embedding endpoint down"))

        await FactStore(repo, llm_client=client).add_fact(
            Fact(symbolic_repr="location.home", natural_lang_repr="At work")
        )

        updates = repo.update.call_args.args[1]
        assert updates == {
            "natural_lang_repr": "At work",
            "payload": {},
            "confidence": 0.5,
        }
        assert "embedding" not in updates
        repo.store.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_store_without_llm_client_skips_embedding(self, repo):
        """FactStore(repo) keeps working unchanged: no client, no embedding call."""
        fact = Fact(symbolic_repr="fact.plain", natural_lang_repr="A plain fact")
        repo.store.return_value = fact

        result = await FactStore(repo).add_fact(fact)

        repo.store.assert_awaited_once_with(fact)
        assert result is fact
        assert fact.embedding is None

    @pytest.mark.asyncio
    async def test_empty_text_keeps_any_existing_embedding_without_a_call(self, repo):
        """With no text to embed, a supplied vector is preserved and the provider is unused."""
        client = FakeEmbeddingClient([0.5, 0.5])
        fact = Fact(symbolic_repr="", natural_lang_repr="", embedding=[0.9, 0.1])
        repo.store.return_value = fact

        await FactStore(repo, llm_client=client).add_fact(fact)

        assert client.texts == []
        assert fact.embedding == [0.9, 0.1]
        repo.store.assert_awaited_once_with(fact)
