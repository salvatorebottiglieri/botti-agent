"""LLM data models."""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class Role(str, Enum):
    """Message role in a conversation."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ChatMessage(BaseModel):
    """A single message in a conversation."""

    role: Role
    content: str | None = None
    name: str | None = None  # For tool messages
    tool_call_id: str | None = None  # For tool result messages

    model_config = {
        "extra": "allow",
    }


class ToolCall(BaseModel):
    """A tool call requested by the LLM."""

    id: str = Field(default_factory=lambda: f"call_{uuid4().hex[:8]}")
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)

    model_config = {
        "extra": "allow",
    }


class ToolDefinition(BaseModel):
    """
    Definition of a tool available to the LLM.

    Uses JSON Schema for input/output validation.
    """

    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] | None = None

    model_config = {
        "extra": "allow",
    }


class UsageStats(BaseModel):
    """Token usage statistics from LLM response."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatResult(BaseModel):
    """Result of a chat completion."""

    message: ChatMessage
    tool_calls: list[ToolCall] | None = None
    usage: UsageStats | None = None
    model: str | None = None
    finish_reason: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = {
        "extra": "allow",
    }
