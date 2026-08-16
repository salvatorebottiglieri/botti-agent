"""Interaction Module - thin interface for receiving requests and formatting responses."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from cortex.sessions import policy

if TYPE_CHECKING:
    from cortex.execution import ExecutionModule
    from cortex.sessions.interfaces import SessionRepository
    from cortex.sessions.models import Session


class InteractionService:
    """
    Thin facade used by the chat route to look up or create a session.

    The agentic loop is invoked directly via ExecutionModule from the route.
    """

    def __init__(
        self,
        execution_module: ExecutionModule,
        session_repository: SessionRepository,
    ):
        self._execution = execution_module
        self._session_repository = session_repository

    async def get_session(self, session_id: UUID) -> Session | None:
        """Get a session by ID."""
        return await self._session_repository.get(session_id)

    async def get_or_create_session(self, session_id: UUID | None) -> Session:
        """Get existing session or create a new ACTIVE one."""
        return await policy.get_or_create_session(self._session_repository, session_id)
