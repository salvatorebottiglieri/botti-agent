"""Persistence model for loop-trace rows (issue #111 T1)."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class TraceEvent(BaseModel):
    """A stored row from the ``loop_events`` table.

    One row per loop event: monotonic ``seq`` within the session, wire
    ``event_type``, and the event's self-describing ``to_dict()`` JSON as an
    opaque ``payload``. The repository stores and returns ``payload``
    verbatim — it has no field knowledge of individual event types.
    """

    id: int
    session_id: UUID
    seq: int
    event_type: str
    payload: dict[str, Any]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
