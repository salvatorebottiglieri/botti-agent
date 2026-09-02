"""PostgreSQL implementation of TraceRepository."""

from datetime import datetime
from typing import Any
from uuid import UUID

from cortex.db.session import DbSession
from cortex.trace.interfaces import TraceRepository
from cortex.trace.models import TraceEvent

_COLUMNS = "id, session_id, seq, event_type, payload, created_at"


class PostgresTraceRepository(TraceRepository):
    """
    PostgreSQL implementation of loop-trace storage.

    Uses the shared db session for connection pooling. ``payload`` is bound
    as opaque JSON (jsonb codec handles serialization); it is read back
    verbatim with no field knowledge.
    """

    async def insert_event(
        self,
        session_id: UUID,
        seq: int,
        event_type: str,
        payload: dict[str, Any],
    ) -> TraceEvent:
        """Persist one loop event and return the stored row."""
        async with DbSession() as db:
            row = await db.fetchrow(
                f"""
                INSERT INTO loop_events (session_id, seq, event_type, payload)
                VALUES ($1, $2, $3, $4)
                RETURNING {_COLUMNS}
                """,
                session_id,
                seq,
                event_type,
                payload,
            )
            return self._row_to_event(row)

    async def list_events(self, session_id: UUID) -> list[TraceEvent]:
        """List a session's loop events in seq order (oldest first)."""
        async with DbSession() as db:
            rows = await db.fetch(
                f"""
                SELECT {_COLUMNS}
                FROM loop_events
                WHERE session_id = $1
                ORDER BY seq
                """,
                session_id,
            )
            return [self._row_to_event(row) for row in rows]

    async def max_seq(self, session_id: UUID) -> int | None:
        """Return the highest seq persisted for a session, or None when the
        session has no ``loop_events`` rows yet (issue #112 T2)."""
        async with DbSession() as db:
            row = await db.fetchrow(
                """
                SELECT MAX(seq) AS max_seq
                FROM loop_events
                WHERE session_id = $1
                """,
                session_id,
            )
            # A bare aggregate always returns one row (NULL when the session
            # has no rows); the None guard narrows the Record | None type.
            if row is None or row["max_seq"] is None:
                return None
            return int(row["max_seq"])

    async def delete_older_than(self, cutoff: datetime) -> int:
        """Delete loop-event rows strictly older than the cutoff.

        Returns the number of rows deleted.
        """
        async with DbSession() as db:
            status = await db.execute(
                "DELETE FROM loop_events WHERE created_at < $1",
                cutoff,
            )
            # asyncpg status string, e.g. "DELETE 3"
            return int(status.split()[-1])

    def _row_to_event(self, row: Any) -> TraceEvent:
        """Convert a database row to a TraceEvent model."""
        return TraceEvent(
            id=row["id"],
            session_id=row["session_id"],
            seq=row["seq"],
            event_type=row["event_type"],
            payload=row["payload"],
            created_at=row["created_at"],
        )
