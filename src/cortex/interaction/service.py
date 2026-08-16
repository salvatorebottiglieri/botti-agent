"""Interaction Module - thin interface for receiving requests and formatting responses."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from cortex.agentic.models import PersonalityContext
from cortex.sessions import policy

if TYPE_CHECKING:
    from cortex.execution import ExecutionModule
    from cortex.sessions.interfaces import SessionRepository
    from cortex.sessions.models import Session

logger = logging.getLogger(__name__)


class PersonalityService:
    """
    Service for managing personality traits and response formatting.

    Derives personality from memory and applies it to responses.
    """

    def __init__(self, memory_service: Any | None = None):
        self._memory = memory_service

    async def get_personality(
        self,
        session_id: UUID | None = None,
    ) -> PersonalityContext:
        """
        Get personality context for a session.

        Args:
            session_id: Current session

        Returns:
            PersonalityContext with formatting traits
        """
        if self._memory and hasattr(self._memory, 'get_personality_context'):
            try:
                personality = await self._memory.get_personality_context(session_id)
                if isinstance(personality, PersonalityContext):
                    return personality
            except Exception as e:
                logger.warning(f"Failed to get personality: {e}")

        # Return default personality
        return PersonalityContext()

    def format_response(
        self,
        text: str,
        *,
        formality: float | None = None,
        verbosity: float | None = None,
    ) -> str:
        """
        Format response text based on personality.

        Args:
            text: Raw response text
            formality: 0-1, higher = more formal
            verbosity: 0-1, higher = longer

        Returns:
            Formatted text
        """
        # Simple formatting rules
        if formality is not None:
            if formality > 0.8:
                # Very formal: expand contractions
                text = text.replace("n't", " not")
                text = text.replace("'re", " are")
                text = text.replace("'ve", " have")
            elif formality < 0.3:
                # Very casual: can use more relaxed language
                pass  # Keep as-is

        return text

    async def update_preferences(
        self,
        session_id: UUID,
        *,
        formality: float | None = None,
        verbosity: float | None = None,
        technical_level: float | None = None,
    ) -> None:
        """
        Update personality preferences based on user feedback.

        Stores in memory for future sessions.
        """
        if not self._memory:
            return

        try:
            from cortex.memory.models import Fact, FactMutability, FactType

            updates = {}
            if formality is not None:
                updates["formality"] = formality
            if verbosity is not None:
                updates["verbosity"] = verbosity
            if technical_level is not None:
                updates["technical_level"] = technical_level

            for key, value in updates.items():
                fact = Fact(
                    type=FactType.USER_PREFERENCE,
                    mutability=FactMutability.MUTABLE,
                    symbolic_repr=f"preference.{key}",
                    natural_lang_repr=f"User prefers {key}={value}",
                    payload={key: value, "session_id": str(session_id)},
                    confidence=0.8,
                )
                await self._memory.store_fact(fact)

        except Exception as e:
            logger.warning(f"Failed to update preferences: {e}")


class InteractionService:
    """
    Thin facade used by the chat route to look up or create a session.

    Holds the personality service and a reference to the session repository.
    The agentic loop is invoked directly via ExecutionModule from the route.
    """

    def __init__(
        self,
        execution_module: ExecutionModule,
        session_repository: SessionRepository,
        personality_service: PersonalityService | None = None,
    ):
        self._execution = execution_module
        self._session_repository = session_repository
        self._personality = personality_service or PersonalityService()

    async def get_session(self, session_id: UUID) -> Session | None:
        """Get a session by ID."""
        return await self._session_repository.get(session_id)

    async def get_or_create_session(self, session_id: UUID | None) -> Session:
        """Get existing session or create a new ACTIVE one."""
        return await policy.get_or_create_session(self._session_repository, session_id)
