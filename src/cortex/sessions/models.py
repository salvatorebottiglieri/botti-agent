"""Session and Message data models."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class SessionState(StrEnum):
    """Session lifecycle states."""
    CREATED = "created"
    ACTIVE = "active"
    IDLE = "idle"
    ENDED = "ended"


class MessageRole(StrEnum):
    """Message sender roles."""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL_RESULT = "tool_result"


class Message(BaseModel):
    """A single message in a conversation."""
    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    role: MessageRole
    content: str
    tool_calls: list[dict[str, Any]] | None = None  # Serialized tool calls
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = ConfigDict(use_enum_values=True)


class Session(BaseModel):
    """A conversation session."""
    id: UUID = Field(default_factory=uuid4)
    state: SessionState = SessionState.CREATED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_activity_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(use_enum_values=True)


class SessionWithMessages(BaseModel):
    """Session with its messages."""
    session: Session
    messages: list[Message]
