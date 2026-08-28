"""Deterministic fake LLM client for eval harness tests.

Lets harness tests drive the real AgentLoop (real Reasoner, real meta
tools) without ever hitting a real API: each ``chat()`` call pops the next
scripted :class:`~cortex.llm.models.ChatResult` off a queue.
"""

from __future__ import annotations

from typing import Any

from cortex.config.models import Settings
from cortex.llm.base import LLMClient
from cortex.llm.config import GenerationConfig
from cortex.llm.models import ChatMessage, ChatResult, Role
from cortex.tools.interfaces import ToolCall, ToolDefinition


class ScriptedLLMClient(LLMClient):
    """Returns scripted responses in order, one per ``chat()`` call."""

    def __init__(self, responses: list[ChatResult] | None = None) -> None:
        self._responses: list[ChatResult] = list(responses or [])
        self.calls: list[list[ChatMessage]] = []

    def queue_tool_call(self, name: str, arguments: dict[str, Any]) -> None:
        """Queue a response that tells the loop to call a tool."""
        self._responses.append(
            ChatResult(
                message=ChatMessage(role=Role.ASSISTANT, content=""),
                tool_calls=[ToolCall(name=name, arguments=arguments)],
            )
        )

    def queue_text(self, text: str) -> None:
        """Queue a response that ends the loop with the given text."""
        self._responses.append(
            ChatResult(message=ChatMessage(role=Role.ASSISTANT, content=text))
        )

    @property
    def exhausted(self) -> bool:
        """True when every scripted response has been consumed."""
        return not self._responses

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolDefinition] | None = None,
        generation_config: GenerationConfig | None = None,
    ) -> ChatResult:
        self.calls.append(list(messages))
        if not self._responses:
            raise AssertionError("ScriptedLLMClient ran out of scripted responses")
        return self._responses.pop(0)

    def translate_tools(self, tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        return [{"name": tool.name} for tool in tools]

    def translate_tool_call(self, raw: dict[str, Any]) -> ToolCall:
        return ToolCall(**raw)

    def get_provider_name(self) -> str:
        return "scripted"

    @classmethod
    def from_settings(cls, settings: Settings) -> LLMClient:
        return cls()
