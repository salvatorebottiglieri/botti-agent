"""Memory module interfaces (ABCs)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any
from uuid import UUID

from cortex.events.base import BaseEvent

from .models import Concept, Fact, FactType


class FactRepository(ABC):
    """
    Persistence interface for facts.

    Implementations store and retrieve facts from a database.
    """

    @abstractmethod
    async def store(self, fact: Fact) -> Fact:
        """
        Store a new fact.

        Args:
            fact: The fact to store.

        Returns:
            The stored fact with updated metadata.
        """
        ...

    @abstractmethod
    async def store_batch(self, facts: list[Fact]) -> list[Fact]:
        """
        Store multiple facts in a batch.

        Args:
            facts: The facts to store.

        Returns:
            The stored facts.
        """
        ...

    @abstractmethod
    async def get(self, fact_id: UUID) -> Fact | None:
        """
        Get a fact by ID.

        Args:
            fact_id: The fact's UUID.

        Returns:
            The fact if found, None otherwise.
        """
        ...

    @abstractmethod
    async def retract(self, fact_id: UUID, reason: str | None = None) -> None:
        """
        Retract (soft-delete) a fact.

        Args:
            fact_id: The fact to retract.
            reason: Optional reason for retraction.
        """
        ...

    @abstractmethod
    async def update(self, fact_id: UUID, updates: dict[str, Any]) -> Fact | None:
        """
        Update a fact's fields.

        Args:
            fact_id: The fact to update.
            updates: Dictionary of field updates.

        Returns:
            The updated fact if found.
        """
        ...

    @abstractmethod
    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        fact_types: list[FactType] | None = None,
        min_confidence: float | None = None,
    ) -> list[Fact]:
        """
        Search for facts by text query.

        Args:
            query: Text to search for.
            limit: Maximum number of results.
            fact_types: Filter by fact types.
            min_confidence: Minimum confidence threshold.

        Returns:
            Matching facts.
        """
        ...

    @abstractmethod
    async def get_by_type(
        self,
        fact_type: FactType,
        *,
        limit: int = 50,
        active_only: bool = True,
    ) -> list[Fact]:
        """
        Get facts by type.

        Args:
            fact_type: The fact type to filter by.
            limit: Maximum number of results.
            active_only: Whether to include retracted facts.

        Returns:
            Facts of the specified type.
        """
        ...

    @abstractmethod
    async def get_recent(self, *, limit: int = 20) -> list[Fact]:
        """
        Get recent facts.

        Args:
            limit: Maximum number of results.

        Returns:
            Recently created facts.
        """
        ...

    @abstractmethod
    async def record_access(self, fact_id: UUID) -> None:
        """
        Record that a fact was accessed.

        Args:
            fact_id: The accessed fact.
        """
        ...

    @abstractmethod
    async def get_by_symbolic_repr(self, symbolic_repr: str) -> Fact | None:
        """
        Get a fact by its symbolic representation.

        Args:
            symbolic_repr: The symbolic representation (e.g., "location.home").

        Returns:
            The fact if found.
        """
        ...


class ConceptRepository(ABC):
    """
    Persistence interface for concepts.
    """

    @abstractmethod
    async def store(self, concept: Concept) -> Concept:
        """Store a new concept."""
        ...

    @abstractmethod
    async def get(self, concept_id: UUID) -> Concept | None:
        """Get a concept by ID."""
        ...

    @abstractmethod
    async def retract(self, concept_id: UUID, reason: str | None = None) -> None:
        """Retract a concept."""
        ...

    @abstractmethod
    async def get_by_symbolic_repr(self, symbolic_repr: str) -> Concept | None:
        """Get a concept by its symbolic representation."""
        ...

    @abstractmethod
    async def get_validated(self, *, limit: int = 50) -> list[Concept]:
        """Get all validated concepts."""
        ...

    @abstractmethod
    async def invalidate_from_fact(self, fact_id: UUID) -> None:
        """
        Invalidate concepts that depend on a fact.

        Args:
            fact_id: The retracted fact.
        """
        ...


class FactExtractor(ABC):
    """
    Extract facts from various sources.

    Implementations use LLM or rules to extract structured facts
    from unstructured data.
    """

    @abstractmethod
    async def extract_from_text(self, text: str) -> list[Fact]:
        """
        Extract facts from free text (LLM-powered).

        Args:
            text: The text to extract from.

        Returns:
            Extracted facts.
        """
        ...

    @abstractmethod
    def extract_from_event(self, event: BaseEvent) -> list[Fact]:
        """
        Extract facts from an event.

        Args:
            event: The event to extract from.

        Returns:
            Extracted facts.
        """
        ...

    @abstractmethod
    def extract_from_event_type(self, event_type: str, payload: dict[str, Any]) -> list[Fact]:
        """
        Extract facts from a raw event type and payload.

        Args:
            event_type: The event type name (e.g. "location").
            payload: The event payload.

        Returns:
            Extracted facts.
        """
        ...
