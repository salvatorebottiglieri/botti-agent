"""Context Builder - assembles reasoning context from all sources."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from cortex.agentic.models import (
    Context,
    Mode,
    GoalContext,
    PersonalityContext,
    AmbientContext,
)
from cortex.memory.models import FactType

if TYPE_CHECKING:
    from cortex.sessions.service import SessionService
    from cortex.services.memory_service import MemoryService
    from cortex.tools.interfaces import ToolRegistry

logger = logging.getLogger(__name__)


class ContextBuilder:
    """
    Assembles context from all sources for LLM reasoning.

    Before each reasoning step, collects:
    1. Conversation history (last N messages)
    2. Relevant facts from Memory
    3. Available tool schemas
    4. Personality context
    5. Goal context (if mode=GOAL)
    6. Ambient context (time, location)
    """

    def __init__(
        self,
        session_service: SessionService,
        memory_service: MemoryService,
        tool_registry: ToolRegistry,
        max_messages: int = 20,
        max_facts: int = 10,
    ):
        self._session_service = session_service
        self._memory_service = memory_service
        self._tool_registry = tool_registry
        self.max_messages = max_messages
        self.max_facts = max_facts

    async def build(
        self,
        session_id: UUID,
        user_message: str,
        mode: Mode,
        *,
        goal_id: UUID | None = None,
        fact_types: list[str] | None = None,
    ) -> Context:
        """
        Build complete reasoning context.

        Args:
            session_id: Current session
            user_message: The new message from user
            mode: CHAT or GOAL mode
            goal_id: Goal ID (for GOAL mode)
            fact_types: Filter facts by these types

        Returns:
            Assembled Context
        """
        # 1. Get conversation history (limit to max_messages - 1 for user message)
        messages = await self._session_service.get_messages(
            session_id, 
            limit=self.max_messages - 1
        )
        
        # 2. Add user message to conversation
        from cortex.sessions.models import Message, MessageRole
        user_msg = Message(
            session_id=session_id,
            role=MessageRole.USER,
            content=user_message,
        )
        messages.append(user_msg)
        
        # 3. Enforce the limit (should never exceed max_messages)
        messages = messages[-self.max_messages:]

        # 3. Get tools
        tools = self._get_tools()

        # 4. Get facts
        facts = await self._get_facts(user_message, session_id, fact_types)

        # 5. Get personality
        personality = await self._get_personality(session_id)

        # 6. Get ambient context
        ambient = await self._get_ambient_context()

        # 7. Get goal context (if GOAL mode)
        goal = None
        if mode == Mode.GOAL and goal_id:
            goal = GoalContext(
                goal_id=goal_id,
                description="",  # Will be filled by reasoner
            )

        return Context(
            session_id=session_id,
            conversation=messages,
            facts=facts,
            tools=tools,
            personality=personality,
            goal=goal,
            ambient=ambient,
        )

    def _get_tools(self) -> list[Any]:
        """Get available tool schemas."""
        try:
            return self._tool_registry.get_schemas()
        except Exception as e:
            logger.warning(f"Failed to get tool schemas: {e}")
            return []

    async def _get_facts(
        self,
        query: str,
        session_id: UUID,
        fact_types: list[str] | None = None,
    ) -> list[Any]:
        """Get relevant facts from memory."""
        try:
            types = None
            if fact_types:
                types = [FactType(t) for t in fact_types]

            return await self._memory_service.get_relevant(
                query=query,
                limit=self.max_facts,
                session_id=session_id,
                fact_types=types,
            )
        except Exception as e:
            logger.warning(f"Failed to get relevant facts: {e}")
            return []

    async def _get_personality(self, session_id: UUID | None) -> PersonalityContext | None:
        """Get personality context."""
        try:
            return await self._memory_service.get_personality_context(session_id)
        except Exception as e:
            logger.warning(f"Failed to get personality context: {e}")
            return None

    async def _get_ambient_context(self) -> AmbientContext | None:
        """Get ambient context."""
        try:
            context_dict = await self._memory_service.get_context(
                dimensions=["time", "location", "activity", "weather"]
            )

            if not context_dict:
                return None

            return AmbientContext(
                time_of_day=context_dict.get("time"),
                location=context_dict.get("location"),
                activity=context_dict.get("activity"),
                weather=context_dict.get("weather"),
            )
        except Exception as e:
            logger.warning(f"Failed to get ambient context: {e}")
            return None

    async def build_quick(
        self,
        session_id: UUID,
        messages: list[Any],
        user_message: str,
    ) -> Context:
        """
        Build context with pre-fetched data.

        Use when you already have the data (faster than build).
        """
        from cortex.sessions.models import Message, MessageRole

        # Add user message
        messages.append(Message(
            session_id=session_id,
            role=MessageRole.USER,
            content=user_message,
        ))

        # Get tools
        tools = self._get_tools()

        return Context(
            session_id=session_id,
            conversation=messages,
            tools=tools,
        )