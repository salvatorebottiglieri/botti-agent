"""Base event models for the event bus."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class EventMetadata(BaseModel):
    """Metadata attached to every event."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_module: str
    trace_id: str | None = None
    session_id: UUID | None = None
    salience: float = Field(default=0.5, ge=0.0, le=1.0)
    correlation_id: str | None = None


class BaseEvent(BaseModel):
    """Base event model — all events inherit from this."""

    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: EventMetadata

    model_config = {
        "extra": "allow",
    }

    @classmethod
    def create(
        cls,
        event_type: str,
        payload: dict[str, Any] | None = None,
        source_module: str = "unknown",
        session_id: UUID | None = None,
        trace_id: str | None = None,
        salience: float = 0.5,
    ) -> "BaseEvent":
        """Factory method to create a new event."""
        return cls(
            type=event_type,
            payload=payload or {},
            metadata=EventMetadata(
                timestamp=datetime.now(UTC),
                source_module=source_module,
                session_id=session_id,
                trace_id=trace_id or str(uuid4()),
                salience=salience,
            ),
        )
