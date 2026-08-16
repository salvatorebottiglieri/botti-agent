"""Pure CRUD layer for facts.

FactStore wraps a FactRepository and owns the dedup-on-write rule.
No LLM calls, no event bus subscriptions, no concept cascade.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from cortex.memory.interfaces import FactRepository
from cortex.memory.models import Fact, FactMutability, FactType

logger = logging.getLogger(__name__)

_LIST_LIMIT = 500


class FactStore:
    """Pure CRUD for facts. No LLM, no event bus, no concept cascade."""

    def __init__(self, fact_repository: FactRepository) -> None:
        self._fact_repo = fact_repository

    async def add_fact(self, fact: Fact) -> Fact:
        """
        Store a fact, deduplicating by symbolic representation.

        An existing active fact with the same symbolic_repr is updated in
        place. The gate is the EXISTING fact's mutability: when the stored
        fact is STATIC, the existing fact is returned unchanged (no
        duplicate insert, no overwrite).
        """
        existing = await self._fact_repo.get_by_symbolic_repr(fact.symbolic_repr)
        if existing:
            if existing.mutability != FactMutability.STATIC:
                fact_updated = await self._fact_repo.update(
                    existing.id,
                    {
                        "natural_lang_repr": fact.natural_lang_repr,
                        "payload": fact.payload,
                        "confidence": fact.confidence,
                    },
                )
                if not fact_updated:
                    logger.warning("Fact update not return the new fact")
                    raise Exception("Fact update not return the new fact")
                return fact_updated
            # Static facts don't get updated
            return existing

        return await self._fact_repo.store(fact)

    async def get_fact(self, fact_id: UUID) -> Fact | None:
        """Get a fact by ID."""
        return await self._fact_repo.get(fact_id)

    async def update_fact(self, fact_id: UUID, updates: dict[str, Any]) -> Fact | None:
        """Update a fact's fields."""
        return await self._fact_repo.update(fact_id, updates)

    async def retract_fact(self, fact_id: UUID, reason: str | None = None) -> bool:
        """Soft-delete a fact. Returns True when the fact existed."""
        existing = await self._fact_repo.get(fact_id)
        if existing is None:
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
