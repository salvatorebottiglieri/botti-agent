"""ContextProvider - bundle seam + cross-repository operations for the Memory module."""

from __future__ import annotations

import logging
from uuid import UUID

from cortex.agentic.models import AmbientContext, MemoryContext, PersonalityContext
from cortex.memory.fact_store import FactStore
from cortex.memory.interfaces import ConceptRepository, FactRepository
from cortex.memory.models import Concept, Fact, FactType

logger = logging.getLogger(__name__)


class ContextProvider:
    """Bundle seam + cross-repository operations.

    No CRUD (delegates to FactStore), no extraction (delegates to
    FactExtractor), no LLM calls.
    """

    def __init__(
        self,
        fact_repository: FactRepository,
        fact_store: FactStore,
        concept_repository: ConceptRepository | None = None,
    ):
        self._fact_repo = fact_repository
        self._fact_store = fact_store
        self._concept_repo = concept_repository

        self._min_relevant_confidence = 0.4

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
            bundle.facts = await self.get_relevant_facts(
                session_id=session_id,
                query=query,
                limit=max_facts,
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
            bundle.ambient = await self.get_ambient_context()
        except Exception as e:
            logger.warning(f"MemoryContext: ambient dimension failed: {e}")
            bundle.degraded_dimensions.append("ambient")

        return bundle

    # ─── Query Methods ───────────────────────────────────────────────────

    async def get_relevant_facts(
        self,
        session_id: UUID | None,
        query: str,
        *,
        limit: int = 10,
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

    async def get_ambient_context(self) -> AmbientContext | None:
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

    # ─── Retraction ──────────────────────────────────────────────────────

    async def retract_fact(self, fact_id: UUID, reason: str | None = None) -> bool:
        """
        Retract a fact (soft delete).

        Also invalidates any concepts that depend on this fact, but only
        when the FactStore reports the fact was actually retracted.
        """
        deleted = await self._fact_store.retract_fact(fact_id, reason)

        # Cascade to concepts
        if deleted and self._concept_repo:
            await self._concept_repo.invalidate_from_fact(fact_id)

        return deleted

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


def _avg(values: list[float], default: float) -> float:
    """Compute average or return default."""
    if not values:
        return default
    return sum(values) / len(values)
