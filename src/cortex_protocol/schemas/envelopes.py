"""Envelopes, metadata, and batch structures for minion events."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from cortex_protocol.schemas.events import MinionEvent


class MinionEventMetadata(BaseModel):
    """Metadata added by minion before sending."""

    minion_id: UUID
    minion_type: str  # "phone", "card", "laptop"
    sequence: int  # Monotonic counter
    batch_id: UUID  # Unique batch ID for this send
    device_time: datetime
    cortex_received_at: datetime | None = None


class MinionEventBatch(BaseModel):
    """Envelope wrapping multiple minion events."""

    metadata: MinionEventMetadata
    events: list[MinionEvent] = []


__all__ = [
    "MinionEventMetadata",
    "MinionEventBatch",
]
