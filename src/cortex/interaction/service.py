"""Interaction Module - thin interface for receiving requests and formatting responses."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from cortex.agentic.models import ChatResponse, Mode, PersonalityContext
from cortex.sessions.models import Message, MessageRole

if TYPE_CHECKING:
    from cortex.execution import ExecutionModule
    from cortex.sessions.service import SessionService

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
                if personality:
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
            from cortex.memory.models import Fact, FactType, FactMutability

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
    Thin interface: receives requests, calls Agentic Loop, formats responses.

    Does NOT contain the loop itself.
    """

    def __init__(
        self,
        execution_module: ExecutionModule,
        session_service: SessionService,
        personality_service: PersonalityService | None = None,
    ):
        self._execution = execution_module
        self._session_service = session_service
        self._personality = personality_service or PersonalityService()

    async def handle_message(
        self,
        session_id: UUID | None,
        content: str,
        mode: Mode = Mode.CHAT,
        *,
        max_iterations: int | None = None,
    ) -> ChatResponse:
        """
        Handle incoming user message.

        Args:
            session_id: Existing session ID (or None for new)
            content: User's message
            mode: CHAT or GOAL mode
            max_iterations: Optional iteration limit

        Returns:
            ChatResponse with formatted message
        """
        # Get or create session
        session = await self._get_or_create_session(session_id)

        # Call execution module
        if mode == Mode.CHAT:
            response = await self._execution.run_chat(
                session_id=session.id,
                user_message=content,
                max_iterations=max_iterations,
            )
        else:
            # GOAL mode
            from cortex.agentic.models import GoalResult
            result: GoalResult = await self._execution.run_goal(
                goal_id=session.id,  # Reuse session ID for goal
                description=content,
            )
            response = ChatResponse(
                message=result.message,
                iterations=result.iterations,
            )

        # Add messages to conversation
        await self._session_service.add_message(
            session.id,
            Message(session_id=session.id, role=MessageRole.USER, content=content)
        )
        await self._session_service.add_message(
            session.id,
            Message(session_id=session.id, role=MessageRole.ASSISTANT, content=response.message)
        )

        # Format response with personality
        personality = await self._personality.get_personality(session.id)
        formatted_message = self._personality.format_response(
            response.message,
            formality=personality.formality,
            verbosity=personality.verbosity,
        )

        response.message = formatted_message

        return response

    async def get_session(self, session_id: UUID) -> Any | None:
        """Get a session by ID."""
        return await self._session_service.get(session_id)

    async def get_conversation_history(
        self,
        session_id: UUID,
        limit: int = 50,
    ) -> list[Message]:
        """Get conversation history for a session."""
        return await self._session_service.get_messages(session_id, limit=limit)

    async def _get_or_create_session(self, session_id: UUID | None) -> Any:
        """Get existing session or create new one."""
        if session_id:
            session = await self._session_service.get(session_id)
            if session:
                return session

        # Create new session
        return await self._session_service.create()

    async def _format_with_personality(
        self,
        text: str,
        session_id: UUID | None,
    ) -> str:
        """Format text with personality context."""
        personality = await self._personality.get_personality(session_id)

        return self._personality.format_response(
            text,
            formality=personality.formality,
            verbosity=personality.verbosity,
        )