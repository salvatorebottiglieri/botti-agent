"""Conversation fact extraction: extract from a user message and store facts.

This is the ``llm`` seam for the evidence system (issue #82). Extraction and
storage failures are logged and swallowed here: a chat turn must never fail
because memory is unavailable or the LLM returned malformed output.
"""

from __future__ import annotations

import logging
from uuid import UUID

from cortex.memory.fact_extractor import FactExtractor
from cortex.memory.fact_store import FactStore
from cortex.memory.models import Fact

logger = logging.getLogger(__name__)


class ConversationFactExtractor:
    """Extract facts from a conversation message and persist them."""

    def __init__(self, fact_extractor: FactExtractor, fact_store: FactStore) -> None:
        self._fact_extractor = fact_extractor
        self._fact_store = fact_store

    async def extract_and_store(
        self,
        text: str,
        *,
        session_id: UUID | None = None,
    ) -> list[Fact]:
        """Extract facts from ``text`` and store them, best-effort.

        Blank text short-circuits without an LLM call. Extraction failures
        (provider errors, malformed JSON) and per-fact storage failures are
        logged and skipped, never raised.
        """
        if not text or not text.strip():
            return []

        try:
            facts = await self._fact_extractor.extract_from_text(text)
        except Exception:
            logger.warning(
                "Conversation fact extraction failed; no facts stored", exc_info=True
            )
            return []

        stored: list[Fact] = []
        for fact in facts:
            if not fact.symbolic_repr:
                continue
            fact.payload["source"] = "conversation"
            if session_id is not None:
                fact.payload["session_id"] = str(session_id)
            try:
                stored.append(await self._fact_store.add_fact(fact))
            except Exception:
                logger.warning(
                    "Storing conversation fact %s failed; continuing",
                    fact.symbolic_repr,
                    exc_info=True,
                )
        return stored
