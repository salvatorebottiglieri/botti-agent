"""LLM data models.

`ToolCall` and `ToolDefinition` are not redefined here — they live in
`cortex.tools.interfaces`. The LLM seam consumes/produces them directly so
there is one type per concept across the codebase.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from cortex.tools.interfaces import ToolCall, ToolDefinition


class Role(StrEnum):
    """Message role in a conversation."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ChatMessage(BaseModel):
    """A single message in a conversation."""

    role: Role
    content: str | None = None
    name: str | None = None
    tool_call_id: str | None = None

    model_config = {
        "extra": "allow",
    }


class UsageStats(BaseModel):
    """Token usage statistics from LLM response."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatResult(BaseModel):
    """Result of a chat completion.

    Holds executor-side ``ToolCall`` instances directly; pydantic is configured
    with ``arbitrary_types_allowed`` so the stdlib dataclass passes through
    without revalidation.
    """

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    message: ChatMessage
    tool_calls: list[ToolCall] | None = None
    usage: UsageStats | None = None
    model: str | None = None
    finish_reason: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


__all__ = [
    "Role",
    "ChatMessage",
    "UsageStats",
    "ChatResult",
    "ToolCall",
    "ToolDefinition",
]
