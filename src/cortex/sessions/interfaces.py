"""Session repository interface - abstract base class."""

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from cortex.sessions.models import Message, MessageRole, Session, SessionState


class SessionRepository(ABC):
    """
    Abstract interface for session persistence.

    Implementations should handle database operations.
    """

    @abstractmethod
    async def create(self) -> Session:
        """Create a new session."""
        ...

    @abstractmethod
    async def get(self, session_id: UUID) -> Session | None:
        """Get a session by ID."""
        ...

    @abstractmethod
    async def update_state(
        self, session_id: UUID, state: SessionState, ended_at: datetime | None = None
    ) -> Session | None:
        """Update session state."""
        ...

    @abstractmethod
    async def update_activity(self, session_id: UUID) -> None:
        """Update last_activity_at timestamp."""
        ...

    @abstractmethod
    async def add_message(
        self,
        session_id: UUID,
        role: MessageRole,
        content: str,
        tool_calls: list[dict] | None = None,
    ) -> Message:
        """Add a message to a session."""
        ...

    @abstractmethod
    async def get_messages(
        self,
        session_id: UUID,
        limit: int = 50,
        before: datetime | None = None,
    ) -> list[Message]:
        """Get messages for a session, newest first."""
        ...

    @abstractmethod
    async def list_active(self, limit: int = 10) -> list[Session]:
        """List active sessions, most recent first."""
        ...
