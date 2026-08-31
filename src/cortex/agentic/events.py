"""LoopEvent models for the streaming agent loop (ADR-0002).

These dataclasses are the public seam between ``AgentLoop`` and its
callers: every progress signal the loop emits is one of these events.
Consumers of this seam (the SSE adapter in a follow-up issue) consume
``event_type`` as the wire name directly — one vocabulary, no mapping
table:

* ``thinking``    — a reasoning step is in progress
* ``text``        — a text delta of the final response
* ``tool_start``  — a tool call is about to execute
* ``tool_done``   — a tool call finished (success or failure)
* ``done``        — the response is complete
* ``ask_user``    — the loop paused to ask the user a clarifying question
* ``error``       — the loop failed

Instances are JSON-ready via :meth:`LoopEvent.to_dict`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar
from uuid import UUID

if TYPE_CHECKING:
    from cortex.llm.models import UsageStats


@dataclass
class LoopEvent:
    """Progress signal from the AgentLoop to its caller (ADR-0002).

    `event_type` is the wire name (one vocabulary, no mapping table).
    """

    event_type: ClassVar[str] = ""
    session_id: UUID

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict; event_type included so payloads are self-describing."""
        payload = asdict(self)
        payload["session_id"] = str(self.session_id)
        return {"event_type": self.event_type, **payload}


@dataclass
class ThinkingEvent(LoopEvent):
    """The loop is emitting a reasoning step before acting."""

    event_type: ClassVar[str] = "thinking"
    message: str


@dataclass
class TextDeltaEvent(LoopEvent):
    """A chunk of the final response text, streamed as it is produced."""

    event_type: ClassVar[str] = "text"
    delta: str


@dataclass
class ToolStartEvent(LoopEvent):
    """A tool call is about to execute.

    `tool_call_id` follows the ``ToolCall.id`` ``call_<hex>`` convention so
    it matches the id later reported by ``ToolResultEvent``.
    """

    event_type: ClassVar[str] = "tool_start"
    tool_name: str
    tool_call_id: str


@dataclass
class ToolResultEvent(LoopEvent):
    """A tool call finished, reporting success or failure and timing."""

    event_type: ClassVar[str] = "tool_done"
    tool_name: str
    tool_call_id: str
    success: bool
    output: str | None = None
    error: str | None = None
    execution_time_ms: float | None = None


@dataclass
class ResponseDoneEvent(LoopEvent):
    """The loop produced its final response.

    `tools_used` carries tool *names* (same semantic as
    ``ChatResponse.tools_used``), not call ids.
    """

    event_type: ClassVar[str] = "done"
    message: str
    tools_used: list[str] = field(default_factory=list)
    iterations: int = 0
    usage: UsageStats | None = None
    latency_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict; usage is a plain dict so payloads stay serializable."""
        payload = super().to_dict()
        if self.usage is not None:
            payload["usage"] = self.usage.model_dump()
        return payload


@dataclass
class AskUserEvent(LoopEvent):
    """The loop is pausing to ask the user a clarifying question.

    Emitted when the model calls the ``ask_user`` tool. `options`, when
    non-empty, are model-suggested answers the UI can render as choices.
    Terminal for the turn, like ``done`` — the loop stops and waits for the
    user's next message.
    """

    event_type: ClassVar[str] = "ask_user"
    question: str
    options: list[str] = field(default_factory=list)


@dataclass
class ErrorEvent(LoopEvent):
    """The loop failed; `code` distinguishes known failure classes."""

    event_type: ClassVar[str] = "error"
    error: str
    code: str | None = None  # reserved: "max_iterations"
