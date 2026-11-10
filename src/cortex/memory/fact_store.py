"""CRUD layer for facts.

FactStore wraps a FactRepository and owns the dedup-on-write rule.
Embeddings are generated eagerly at write time through an optional
LLMClient (ADR-0008). When no client is configured, or the provider fails,
a new fact is stored without an embedding and search falls back to ILIKE.
No event bus subscriptions, no concept cascade.

When an evidence store is configured, writing a fact also records the
observation and lets the evidence engine own ``facts.confidence`` (ADR-0013,
issue #83). Static facts stay outside that machinery: pinned at 1.0, no
evidence rows, never retracted.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from cortex.memory.evidence_store import PRIOR, Observation
from cortex.memory.interfaces import FactRepository
from cortex.memory.models import Fact, FactMutability, FactType

if TYPE_CHECKING:
    from cortex.llm.base import LLMClient
    from cortex.memory.evidence_store import PostgresEvidenceStore

logger = logging.getLogger(__name__)

_LIST_LIMIT = 500


def _utcnow() -> datetime:
    """Observation timestamp; a seam so tests can pin the clock."""
    return datetime.now(UTC)


class FactStore:
    """CRUD for facts; owns dedup-on-write and eager embedding generation."""

    def __init__(
        self,
        fact_repository: FactRepository,
        llm_client: LLMClient | None = None,
        evidence_store: PostgresEvidenceStore | None = None,
    ) -> None:
        self._fact_repo = fact_repository
        self._llm_client = llm_client
        self._evidence_store = evidence_store

    async def _embed(self, fact: Fact) -> list[float] | None:
        """Return the fact's embedding, generating it when a client is configured.

        Prefers the natural-language representation and falls back to the
        symbolic one. A provider failure is logged and yields ``None`` so fact
        storage never fails because embeddings are down.
        """
        text = fact.natural_lang_repr or fact.symbolic_repr
        if self._llm_client is None or not text:
            return fact.embedding

        try:
            return await self._llm_client.embed(text)
        except Exception:
            logger.warning(
                "Embedding generation failed for fact %s; storing without an embedding",
                fact.id,
                exc_info=True,
            )
            return None

    async def add_fact(
        self,
        fact: Fact,
        *,
        source_type: str = "llm",
        source_id: str = "",
    ) -> Fact:
        """
        Store a fact, deduplicating by symbolic representation.

        An existing active fact with the same symbolic_repr is updated in
        place. The gate is the EXISTING fact's mutability: when the stored
        fact is STATIC, the existing fact is returned unchanged (no
        duplicate insert, no overwrite). Existing mutability wins: an
        incoming STATIC observation cannot promote an existing non-static
        fact. Embeddings are generated before the write; a failed generation
        on an update leaves the stored vector untouched.

        With an evidence store configured, the observation is recorded and
        the store owns ``facts.confidence``: a new fact is stored at the prior
        before the first observation is applied, and an existing fact's update
        omits ``confidence`` so the recompute is the only writer. Without one
        the legacy last-writer-wins confidence update is preserved.
        """
        existing = await self._fact_repo.get_by_symbolic_repr(fact.symbolic_repr)
        if existing:
            existing_mutability = FactMutability(existing.mutability)
            if existing_mutability != FactMutability.STATIC:
                embedding = await self._embed(fact)
                updates: dict[str, Any] = {
                    "natural_lang_repr": fact.natural_lang_repr,
                    "payload": fact.payload,
                }
                if self._evidence_store is None:
                    updates["confidence"] = (
                        existing.confidence
                        if FactMutability(fact.mutability) == FactMutability.STATIC
                        else fact.confidence
                    )
                if embedding is not None:
                    updates["embedding"] = embedding
                fact_updated = await self._fact_repo.update(existing.id, updates)
                if not fact_updated:
                    logger.warning("Fact update not return the new fact")
                    raise Exception("Fact update not return the new fact")

                if self._evidence_store is None:
                    return fact_updated

                # The observation targets the existing row: its id and its
                # mutability are authoritative (an incoming STATIC does not
                # promote a non-static fact, it is recorded as evidence).
                fact.id = existing.id
                fact.mutability = existing_mutability
                await self._evidence_store.record(
                    fact,
                    Observation(
                        source_type=source_type,
                        source_id=source_id,
                        observed_at=_utcnow(),
                    ),
                )
                refreshed = await self._fact_repo.get(existing.id)
                return refreshed or fact_updated
            # Static facts don't get updated
            return existing

        fact.embedding = await self._embed(fact)
        if self._evidence_store is None:
            return await self._fact_repo.store(fact)

        if FactMutability(fact.mutability) == FactMutability.STATIC:
            # Axioms are pinned from birth and stay outside the evidence engine.
            fact.confidence = 1.0
            return await self._fact_repo.store(fact)

        now = _utcnow()
        # The observation's strength is the ingested confidence; the stored
        # fact starts at the prior and the first observation uses the same
        # rule as every later one.
        strength = fact.confidence
        fact.confidence = PRIOR
        stored = await self._fact_repo.store(fact)
        stored.confidence = strength  # in-memory strength passed to record()
        await self._evidence_store.record(
            stored,
            Observation(source_type=source_type, source_id=source_id, observed_at=now),
        )
        refreshed = await self._fact_repo.get(stored.id)
        return refreshed or stored

    async def get_fact(self, fact_id: UUID) -> Fact | None:
        """Get a fact by ID."""
        return await self._fact_repo.get(fact_id)

    async def update_fact(self, fact_id: UUID, updates: dict[str, Any]) -> Fact | None:
        """Update a fact's fields."""
        return await self._fact_repo.update(fact_id, updates)

    async def retract_fact(self, fact_id: UUID, reason: str | None = None) -> bool:
        """Soft-delete a fact. Returns True when the fact existed and was retracted.

        Static facts are never retracted: they are axioms, so a retraction
        request is refused with a warning and returns False.
        """
        existing = await self._fact_repo.get(fact_id)
        if existing is None:
            return False
        if FactMutability(existing.mutability) == FactMutability.STATIC:
            logger.warning("Refusing to retract static fact %s", fact_id)
            return False
        await self._fact_repo.retract(fact_id, reason)
        return True

    async def list_facts(self, session_id: UUID, fact_type: FactType | None = None) -> list[Fact]:
        """List facts for a session, optionally filtered by fact type.

        The session filter is applied in the repository so the row limit
        applies after filtering (no in-memory truncation).
        """
        return await self._fact_repo.list_by_session(
            session_id=session_id,
            fact_type=fact_type,
            limit=_LIST_LIMIT,
        )
