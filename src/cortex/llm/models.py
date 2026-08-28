"""LLM data models.

`ToolCall` and `ToolDefinition` are not redefined here — they live in
`cortex.tools.interfaces`. The LLM seam consumes/produces them directly so
there is one type per concept across the codebase.
"""

from __future__ import annotations

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
    tool_calls: list[ToolCall] | None = None  # Assistant tool-call turns (internal type)

    model_config = {
        "extra": "allow",
    }


class UsageStats(BaseModel):
    """Token usage statistics from LLM response."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: UsageStats) -> UsageStats:
        """Accumulate usage across calls: sums each token counter."""
        return UsageStats(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


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
