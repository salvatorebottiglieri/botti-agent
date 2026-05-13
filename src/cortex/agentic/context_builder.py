"""Context Builder - assembles reasoning context from all sources."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from cortex.agentic.models import (
    Context,
    Mode,
    GoalContext,
)
from cortex.memory.models import FactType

if TYPE_CHECKING:
    from cortex.sessions.interfaces import SessionRepository
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
        session_repository: SessionRepository,
        memory_service: MemoryService,
        tool_registry: ToolRegistry,
        max_messages: int = 20,
        max_facts: int = 10,
    ):
        self._session_repository = session_repository
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
        messages = await self._session_repository.get_messages(
            session_id,
            limit=self.max_messages - 1,
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

        # 4. Get everything Memory contributes in one call
        types = [FactType(t) for t in fact_types] if fact_types else None
        memory_ctx = await self._memory_service.get_memory_context(
            session_id=session_id,
            query=user_message,
            max_facts=self.max_facts,
            fact_types=types,
        )
        if memory_ctx.degraded_dimensions:
            logger.warning(
                f"MemoryContext degraded: {memory_ctx.degraded_dimensions}"
            )

        # 5. Get goal context (if GOAL mode)
        goal = None
        if mode == Mode.GOAL and goal_id:
            goal = GoalContext(
                goal_id=goal_id,
                description="",  # Will be filled by reasoner
            )

        return Context(
            session_id=session_id,
            conversation=messages,
            tools=tools,
            memory=memory_ctx,
            goal=goal,
        )

    def _get_tools(self) -> list[Any]:
        """Get available tool schemas."""
        try:
            return self._tool_registry.get_schemas()
        except Exception as e:
            logger.warning(f"Failed to get tool schemas: {e}")
            return []

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