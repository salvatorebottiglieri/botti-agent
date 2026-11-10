"""Backfill embeddings for facts written before the provider existed (ADR-0008, #159)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from cortex.llm.base import LLMClient
from cortex.memory.interfaces import FactRepository

logger = logging.getLogger(__name__)


@dataclass
class BackfillStats:
    """Counts for one backfill run."""

    scanned: int = 0
    updated: int = 0
    failed: int = 0


class EmbeddingBackfillRunner:
    """Embed facts whose ``embedding`` is still NULL, in bounded batches.

    The repository's null predicate is the idempotency guarantee: a second run
    sees only rows that are still null, including the ones that failed here.
    A batch is written as one ``backfill_embeddings`` call with only the
    successful pairs, in fact order.
    """

    def __init__(
        self,
        fact_repository: FactRepository,
        llm_client: LLMClient,
        *,
        batch_size: int = 100,
    ) -> None:
        if batch_size < 1:
            raise ValueError(f"batch_size must be at least 1, got {batch_size}")
        self._fact_repository = fact_repository
        self._llm_client = llm_client
        self._batch_size = batch_size

    async def run(self) -> BackfillStats:
        """Page through null-embedding facts until the table has none left."""
        stats = BackfillStats()
        after_id: UUID | None = None

        while True:
            facts = await self._fact_repository.list_missing_embeddings(
                limit=self._batch_size, after_id=after_id
            )
            if not facts:
                break

            stats.scanned += len(facts)
            ids: list[UUID] = []
            vectors: list[list[float]] = []

            for fact in facts:
                text = fact.natural_lang_repr or fact.symbolic_repr
                if not text:
                    logger.warning(
                        "Fact %s has no natural or symbolic representation; left unembedded",
                        fact.id,
                    )
                    stats.failed += 1
                    continue
                try:
                    vector = await self._llm_client.embed(text)
                except Exception as error:
                    logger.warning("Embedding failed for fact %s: %s", fact.id, error)
                    stats.failed += 1
                    continue
                ids.append(fact.id)
                vectors.append(vector)

            if ids:
                await self._fact_repository.backfill_embeddings(ids, vectors)
                stats.updated += len(ids)

            # Advance even when every embed failed, or a provider outage would
            # re-read the same full batch forever.
            after_id = facts[-1].id
            if len(facts) < self._batch_size:
                break

        return stats
