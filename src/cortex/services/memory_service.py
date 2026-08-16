"""Memory Service - high-level interface for the Memory module."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from cortex.agentic.models import AmbientContext, MemoryContext, PersonalityContext
from cortex.memory.fact_store import FactStore
from cortex.memory.interfaces import ConceptRepository, FactExtractor, FactRepository
from cortex.memory.models import Concept, Fact, FactType

logger = logging.getLogger(__name__)


class MemoryService:
    """
    Service API for the Memory Module.

    Provides a high-level interface for:
    - Storing and retrieving facts
    - Extracting facts from conversations
    - Building derived concepts
    - Getting personality and ambient context
    """

    def __init__(
        self,
        fact_repository: FactRepository,
        fact_extractor: FactExtractor | None = None,
        concept_repository: ConceptRepository | None = None,
        event_bus: Any | None = None,
        llm_client: Any | None = None,
    ):
        self._fact_repo = fact_repository
        self._fact_store = FactStore(fact_repository)
        self._extractor = fact_extractor
        self._concept_repo = concept_repository
        self._event_bus = event_bus
        self._llm_client = llm_client

        self._default_confidence = 0.7
        self._min_relevant_confidence = 0.4

    # ─── Storage Methods ─────────────────────────────────────────────────

    async def store_fact(self, fact: Fact) -> Fact:
        """
        Store a new fact.

        Deduplication by symbolic representation is handled by FactStore.
        """
        return await self._fact_store.add_fact(fact)

    async def store_batch(self, facts: list[Fact]) -> list[Fact]:
        """Store multiple facts (dedup applied per fact)."""
        return [await self._fact_store.add_fact(fact) for fact in facts]

    async def retract_fact(self, fact_id: UUID, reason: str | None = None) -> bool:
        """
        Retract a fact (soft delete).

        Also invalidates any concepts that depend on this fact.
        """
        deleted = await self._fact_store.retract_fact(fact_id, reason)

        # Cascade to concepts
        if self._concept_repo:
            await self._concept_repo.invalidate_from_fact(fact_id)

        return deleted

    # ─── Bundle Seam ─────────────────────────────────────────────────────

    async def get_memory_context(
        self,
        session_id: UUID | None,
        query: str,
        *,
        max_facts: int = 10,
        fact_types: list[FactType] | None = None,
    ) -> MemoryContext:
        """
        Single call that returns everything Memory contributes to a reasoning step.

        Failures in any one dimension (facts, personality, ambient) are caught
        and recorded in `degraded_dimensions` rather than raising.
        """
        bundle = MemoryContext()

        try:
            bundle.facts = await self._get_relevant_facts(
                query=query,
                limit=max_facts,
                session_id=session_id,
                fact_types=fact_types,
            )
        except Exception as e:
            logger.warning(f"MemoryContext: facts dimension failed: {e}")
            bundle.degraded_dimensions.append("facts")

        try:
            bundle.personality = await self.get_personality_context(session_id)
        except Exception as e:
            logger.warning(f"MemoryContext: personality dimension failed: {e}")
            bundle.degraded_dimensions.append("personality")

        try:
            bundle.ambient = await self._get_ambient_context()
        except Exception as e:
            logger.warning(f"MemoryContext: ambient dimension failed: {e}")
            bundle.degraded_dimensions.append("ambient")

        return bundle

    # ─── Query Methods ───────────────────────────────────────────────────

    async def _get_relevant_facts(
        self,
        query: str,
        *,
        limit: int = 10,
        session_id: UUID | None = None,
        fact_types: list[FactType] | None = None,
        min_confidence: float | None = None,
    ) -> list[Fact]:
        """
        Get facts relevant to a query.

        Strategy:
        1. Search repository by text
        2. Boost facts from current session
        3. Boost recent facts
        4. Boost high-confidence facts
        """
        # Search repository
        facts = await self._fact_repo.search(
            query,
            limit=limit * 2,  # Get more, filter down
            fact_types=fact_types,
            min_confidence=min_confidence or self._min_relevant_confidence,
        )

        # Boost and sort
        scored_facts = []
        for fact in facts:
            score = self._calculate_relevance_score(fact, query, session_id)
            scored_facts.append((score, fact))

        # Sort by score descending
        scored_facts.sort(key=lambda x: x[0], reverse=True)

        # Return top N
        return [fact for _, fact in scored_facts[:limit]]

    def _calculate_relevance_score(self, fact: Fact, query: str, session_id: UUID | None) -> float:
        """Calculate relevance score for a fact."""
        score = fact.confidence

        # Boost if query appears in text
        query_lower = query.lower()
        if query_lower in fact.symbolic_repr.lower():
            score += 0.3
        if query_lower in fact.natural_lang_repr.lower():
            score += 0.2

        # Boost session facts
        if session_id and fact.payload.get("session_id"):
            if str(fact.payload["session_id"]) == str(session_id):
                score += 0.25

        # Boost recent facts
        if fact.last_accessed_at:
            # More recent = higher boost
            import time

            age_hours = (time.time() - fact.last_accessed_at.timestamp()) / 3600
            if age_hours < 1:
                score += 0.15
            elif age_hours < 24:
                score += 0.1

        # Boost frequently accessed
        if fact.access_count > 5:
            score += 0.1

        return score

    async def _get_ambient_context(self) -> AmbientContext | None:
        """
        Get current ambient context (time, location, activity, weather).

        Returns None when no ambient facts exist; otherwise an AmbientContext
        with whichever dimensions were populated.
        """
        time_facts = await self._fact_repo.get_by_type(FactType.TIME, limit=1)
        loc_facts = await self._fact_repo.get_by_type(FactType.LOCATION, limit=1)
        act_facts = await self._fact_repo.get_by_type(FactType.ACTIVITY, limit=1)
        weather_facts = await self._fact_repo.get_by_type(FactType.WEATHER, limit=1)

        if not any((time_facts, loc_facts, act_facts, weather_facts)):
            return None

        return AmbientContext(
            time_of_day=time_facts[0].natural_lang_repr if time_facts else None,
            location=loc_facts[0].natural_lang_repr if loc_facts else None,
            activity=act_facts[0].natural_lang_repr if act_facts else None,
            weather=weather_facts[0].natural_lang_repr if weather_facts else None,
        )

    async def get_personality_context(self, session_id: UUID | None = None) -> PersonalityContext:
        """
        Get personality traits for response formatting.

        Derived from USER_PREFERENCE facts in memory.
        """
        prefs = await self._fact_repo.get_by_type(FactType.USER_PREFERENCE, limit=20)

        # Aggregate preferences
        formality_scores = []
        verbosity_scores = []
        technical_scores = []

        for pref in prefs:
            payload = pref.payload or {}
            if "formality" in payload:
                formality_scores.append(float(payload["formality"]))
            if "verbosity" in payload:
                verbosity_scores.append(float(payload["verbosity"]))
            if "technical_level" in payload:
                technical_scores.append(float(payload["technical_level"]))

        # Compute averages
        return PersonalityContext(
            formality=_avg(formality_scores, 0.5),
            verbosity=_avg(verbosity_scores, 0.5),
            technical_level=_avg(technical_scores, 0.5),
            humor_level=_avg([], 0.5),  # Default
            empathy=_avg([], 0.5),
            directness=_avg([], 0.5),
        )

    # ─── Fact Extraction ─────────────────────────────────────────────────

    async def extract_from_conversation(
        self, text: str, session_id: UUID | None = None
    ) -> list[Fact]:
        """
        Extract facts from conversation text.

        Uses the FactExtractor if available.
        """
        if not self._extractor:
            return []

        facts = await self._extractor.extract_from_text(text)

        # Store extracted facts
        for fact in facts:
            await self.store_fact(fact)

        return facts

    async def handle_event(self, event: Any) -> None:
        """
        Process an event, extracting facts if applicable.

        Delegates extraction to the FactExtractor; unknown event types
        yield no facts.
        """
        event_type = getattr(event, "type", None)
        payload = getattr(event, "payload", {})

        if not isinstance(event_type, str):
            return

        if not self._extractor:
            return

        for fact in self._extractor.extract_from_event_type(event_type, payload):
            await self.store_fact(fact)

    # ─── Concept Management ───────────────────────────────────────────────

    async def build_concept(
        self,
        symbolic_repr: str,
        natural_lang_repr: str,
        source_facts: list[Fact],
        derivation_method: str = "manual",
        proof_chain: str = "",
    ) -> Concept:
        """
        Build a derived concept from facts.

        Args:
            symbolic_repr: Structured identifier (e.g., "concept.user_routine")
            natural_lang_repr: Human-readable description
            source_facts: Facts this concept is derived from
            derivation_method: How it was derived ("llm_inference", "rule_based", etc.)
            proof_chain: Explanation of derivation

        Returns:
            The created concept with computed confidence
        """
        # Compute confidence as weighted average of source facts
        confidence = (
            sum(f.confidence for f in source_facts) / len(source_facts) if source_facts else 0.5
        )

        concept = Concept(
            symbolic_repr=symbolic_repr,
            natural_lang_repr=natural_lang_repr,
            derivation_method=derivation_method,
            proof_chain=proof_chain or self._build_proof_chain(source_facts),
            source_facts=[f.id for f in source_facts],
            confidence=confidence,
            validated=False,
        )

        if self._concept_repo:
            concept = await self._concept_repo.store(concept)

        return concept

    def _build_proof_chain(self, facts: list[Fact]) -> str:
        """Build a proof chain from source facts."""
        if not facts:
            return "No source facts available."

        chain_parts = []
        for i, fact in enumerate(facts, 1):
            chain_parts.append(f"{i}. {fact.natural_lang_repr} (confidence: {fact.confidence:.2f})")

        return "Based on the following observations:\n" + "\n".join(chain_parts)

    # ─── Search & Retrieval ─────────────────────────────────────────────

    async def search_facts(
        self,
        query: str,
        *,
        fact_types: list[FactType] | None = None,
        min_confidence: float | None = None,
        limit: int = 10,
    ) -> list[Fact]:
        """Search facts by text query."""
        return await self._fact_repo.search(
            query, limit=limit, fact_types=fact_types, min_confidence=min_confidence
        )

    async def get_recent_facts(self, *, limit: int = 20) -> list[Fact]:
        """Get recently stored facts."""
        return await self._fact_repo.get_recent(limit=limit)

    async def get_facts_by_type(
        self, fact_type: FactType, *, limit: int = 50, active_only: bool = True
    ) -> list[Fact]:
        """Get facts of a specific type."""
        return await self._fact_repo.get_by_type(fact_type, limit=limit, active_only=active_only)


def _avg(values: list[float], default: float) -> float:
    """Compute average or return default."""
    if not values:
        return default
    return sum(values) / len(values)
