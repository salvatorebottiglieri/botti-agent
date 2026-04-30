"""PostgreSQL implementation of SessionRepository."""

from datetime import UTC, datetime
from uuid import UUID

from cortex.db.session import DbSession
from cortex.sessions.interfaces import SessionRepository
from cortex.sessions.models import Session, SessionState, Message, MessageRole


class PostgresSessionRepository(SessionRepository):
    """
    PostgreSQL implementation of session storage.
    
    Uses the shared db session for connection pooling.
    """

    async def create(self) -> Session:
        """Create a new session in the database."""
        async with DbSession() as db:
            row = await db.fetchrow(
                """
                INSERT INTO sessions (state)
                VALUES ($1)
                RETURNING id, state, created_at, last_activity_at, ended_at, metadata
                """,
                SessionState.CREATED.value,
            )
            return self._row_to_session(row)

    async def get(self, session_id: UUID) -> Session | None:
        """Get a session by ID."""
        async with DbSession() as db:
            row = await db.fetchrow(
                """
                SELECT id, state, created_at, last_activity_at, ended_at, metadata
                FROM sessions
                WHERE id = $1
                """,
                session_id,
            )
            if row is None:
                return None
            return self._row_to_session(row)

    async def update_state(
        self,
        session_id: UUID,
        state: SessionState,
        ended_at: datetime | None = None,
    ) -> Session | None:
        """Update session state."""
        async with DbSession() as db:
            row = await db.fetchrow(
                """
                UPDATE sessions
                SET state = $1, ended_at = $2
                WHERE id = $3
                RETURNING id, state, created_at, last_activity_at, ended_at, metadata
                """,
                state.value,
                ended_at,
                session_id,
            )
            if row is None:
                return None
            return self._row_to_session(row)

    async def update_activity(self, session_id: UUID) -> None:
        """Update last_activity_at timestamp."""
        async with DbSession() as db:
            await db.execute(
                """
                UPDATE sessions
                SET last_activity_at = $1
                WHERE id = $2
                """,
                datetime.now(UTC),
                session_id,
            )

    async def add_message(
        self,
        session_id: UUID,
        role: MessageRole,
        content: str,
        tool_calls: list[dict] | None = None,
    ) -> Message:
        """Add a message to a session."""
        async with DbSession() as db:
            row = await db.fetchrow(
                """
                INSERT INTO messages (session_id, role, content, tool_calls)
                VALUES ($1, $2, $3, $4)
                RETURNING id, session_id, role, content, tool_calls, created_at
                """,
                session_id,
                role.value,
                content,
                tool_calls,
            )
            # Also update session activity
            await db.execute(
                "UPDATE sessions SET last_activity_at = $1 WHERE id = $2",
                datetime.now(UTC),
                session_id,
            )
            return self._row_to_message(row)

    async def get_messages(
        self,
        session_id: UUID,
        limit: int = 50,
        before: datetime | None = None,
    ) -> list[Message]:
        """Get messages for a session, newest first."""
        async with DbSession() as db:
            if before:
                rows = await db.fetch(
                    """
                    SELECT id, session_id, role, content, tool_calls, created_at
                    FROM messages
                    WHERE session_id = $1 AND created_at < $2
                    ORDER BY created_at DESC
                    LIMIT $3
                    """,
                    session_id,
                    before,
                    limit,
                )
            else:
                rows = await db.fetch(
                    """
                    SELECT id, session_id, role, content, tool_calls, created_at
                    FROM messages
                    WHERE session_id = $1
                    ORDER BY created_at DESC
                    LIMIT $2
                    """,
                    session_id,
                    limit,
                )
            # Return in chronological order (oldest first)
            return [self._row_to_message(row) for row in reversed(rows)]

    async def list_active(self, limit: int = 10) -> list[Session]:
        """List active sessions, most recent first."""
        async with DbSession() as db:
            rows = await db.fetch(
                """
                SELECT id, state, created_at, last_activity_at, ended_at, metadata
                FROM sessions
                WHERE state IN ('created', 'active', 'idle')
                ORDER BY last_activity_at DESC
                LIMIT $1
                """,
                limit,
            )
            return [self._row_to_session(row) for row in rows]

    def _row_to_session(self, row) -> Session:
        """Convert a database row to a Session model."""
        return Session(
            id=row["id"],
            state=row["state"],
            created_at=row["created_at"],
            last_activity_at=row["last_activity_at"],
            ended_at=row["ended_at"],
            metadata=row["metadata"] or {},
        )

    def _row_to_message(self, row) -> Message:
        """Convert a database row to a Message model."""
        return Message(
            id=row["id"],
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            tool_calls=row["tool_calls"],
            created_at=row["created_at"],
        )
