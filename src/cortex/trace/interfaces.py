"""Trace repository interface - abstract base class (issue #111 T1)."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any
from uuid import UUID

from cortex.trace.models import TraceEvent


class TraceRepository(ABC):
    """
    Abstract interface for loop-trace persistence.

    Persistence primitives for the ``loop_events`` table. ``payload`` is an
    event's self-describing ``to_dict()`` JSON and is treated as opaque —
    implementations must not inspect its fields.

    ``max_seq`` is the cross-turn seq-continuity primitive (issue #112 T2):
    the recorder starts each capture at ``max_seq + 1`` so seq stays monotonic
    per session across turns under ``UNIQUE(session_id, seq)``.
    """

    @abstractmethod
    async def insert_event(
        self,
        session_id: UUID,
        seq: int,
        event_type: str,
        payload: dict[str, Any],
    ) -> TraceEvent:
        """Persist one loop event for a session and return the stored row."""
        ...

    @abstractmethod
    async def list_events(self, session_id: UUID) -> list[TraceEvent]:
        """List a session's loop events in seq order (oldest first)."""
        ...

    @abstractmethod
    async def max_seq(self, session_id: UUID) -> int | None:
        """Return the highest seq persisted for a session, or None when the
        session has no ``loop_events`` rows yet."""
        ...

    @abstractmethod
    async def delete_older_than(self, cutoff: datetime) -> int:
        """Delete loop-event rows older than the cutoff; return the count.

        Rows whose created_at is at or after the cutoff are never touched.
        """
        ...
