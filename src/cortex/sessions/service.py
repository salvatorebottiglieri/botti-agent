"""Session service - high-level session operations."""

from datetime import datetime, timezone
from uuid import UUID

from cortex.sessions.interfaces import SessionRepository
from cortex.sessions.models import Session, SessionState, Message, MessageRole, SessionWithMessages


class SessionService:
    """
    High-level session operations.
    
    Wraps the repository with business logic.
    """

    # Timeout for idle sessions (in minutes)
    IDLE_TIMEOUT_MINUTES = 5
    # Timeout before ending idle sessions (in minutes)
    END_TIMEOUT_MINUTES = 30

    def __init__(self, repository: SessionRepository):
        self._repository = repository

    async def create_session(self) -> Session:
        """Create a new active session."""
        session = await self._repository.create()
        return await self._repository.update_state(session.id, SessionState.ACTIVE)

    async def get_session(self, session_id: UUID) -> Session | None:
        """Get a session by ID."""
        return await self._repository.get(session_id)

    async def resume_session(self, session_id: UUID) -> Session | None:
        """Resume an idle session, making it active again."""
        session = await self._repository.get(session_id)
        if session is None:
            return None
        
        if session.state == SessionState.IDLE:
            return await self._repository.update_state(session_id, SessionState.ACTIVE)
        elif session.state == SessionState.ENDED:
            # Can't resume ended sessions
            return None
        
        return session

    async def end_session(self, session_id: UUID) -> Session | None:
        """End a session."""
        return await self._repository.update_state(
            session_id, 
            SessionState.ENDED,
            ended_at=datetime.now(timezone.utc)
        )

    async def add_user_message(self, session_id: UUID, content: str) -> Message:
        """Add a user message to a session."""
        # First ensure session is active
        session = await self._repository.get(session_id)
        if session and session.state == SessionState.IDLE:
            await self._repository.update_state(session_id, SessionState.ACTIVE)
        
        return await self._repository.add_message(
            session_id=session_id,
            role=MessageRole.USER,
            content=content,
        )

    async def add_assistant_message(
        self, 
        session_id: UUID, 
        content: str,
        tool_calls: list[dict] | None = None
    ) -> Message:
        """Add an assistant message to a session."""
        return await self._repository.add_message(
            session_id=session_id,
            role=MessageRole.ASSISTANT,
            content=content,
            tool_calls=tool_calls,
        )

    async def add_tool_result(
        self,
        session_id: UUID,
        content: str,
        tool_calls: list[dict] | None = None
    ) -> Message:
        """Add a tool result message to a session."""
        return await self._repository.add_message(
            session_id=session_id,
            role=MessageRole.TOOL_RESULT,
            content=content,
            tool_calls=tool_calls,
        )

    async def get_conversation(
        self,
        session_id: UUID,
        limit: int = 50
    ) -> SessionWithMessages | None:
        """Get a session with its messages."""
        session = await self._repository.get(session_id)
        if session is None:
            return None
        
        messages = await self._repository.get_messages(session_id, limit=limit)
        return SessionWithMessages(session=session, messages=messages)

    async def list_active_sessions(self, limit: int = 10) -> list[Session]:
        """List active sessions."""
        return await self._repository.list_active(limit=limit)

    async def check_idle_sessions(self) -> list[UUID]:
        """
        Check for idle sessions and mark them as idle.
        
        Returns list of session IDs that were marked idle.
        """
        # For now, just update activity timestamps
        # Full idle detection would require tracking last message time
        active_sessions = await self._repository.list_active(limit=100)
        marked_idle = []
        
        for session in active_sessions:
            if session.state == SessionState.ACTIVE:
                # Simple check: if last activity > IDLE_TIMEOUT_MINUTES ago
                # (in real impl, you'd track last message time)
                pass
        
        return marked_idle
