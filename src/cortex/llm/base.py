"""Abstract base class for LLM clients."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from cortex.config.models import Settings
from cortex.llm.config import GenerationConfig
from cortex.llm.models import ChatMessage, ChatResult
from cortex.tools.interfaces import ToolCall, ToolDefinition


class LLMClient(ABC):
    """
    Provider-agnostic LLM interface.

    All LLM providers implement this interface to ensure
    consistent behavior across different backends.
    """

    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolDefinition] | None = None,
        generation_config: GenerationConfig | None = None,
    ) -> ChatResult:
        """
        Send chat to LLM, return response.

        Args:
            messages: Conversation history
            tools: Optional list of tool definitions for function calling
            generation_config: Optional generation parameters

        Returns:
            ChatResult with response message and optional tool calls
        """
        ...

    @abstractmethod
    def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolDefinition] | None = None,
        generation_config: GenerationConfig | None = None,
    ) -> AsyncIterator[str | ChatResult]:
        """
        Stream a chat completion.

        Yields text deltas (``str``) as the provider produces them, then a
        single final ``ChatResult`` as the terminal item — content, tool calls
        and usage assembled from the stream. Callers iterate and collect the
        ``str`` deltas; the only non-``str`` item, yielded last, is the
        complete ``ChatResult``.

        Args:
            messages: Conversation history
            tools: Optional list of tool definitions for function calling
            generation_config: Optional generation parameters

        Yields:
            ``str`` text deltas, then a final ``ChatResult``.
        """
        ...

    @abstractmethod
    def translate_tools(self, tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        """
        Convert internal tool schema to provider format.

        Args:
            tools: Internal tool definitions

        Returns:
            Provider-specific tool schema
        """
        ...

    @abstractmethod
    def translate_tool_call(self, raw: dict[str, Any]) -> ToolCall:
        """
        Convert provider tool call response to internal schema.

        Args:
            raw: Raw tool call from provider

        Returns:
            Internal ToolCall object
        """
        ...

    @abstractmethod
    def get_provider_name(self) -> str:
        """Return the provider name (e.g., 'openai', 'anthropic')."""
        ...

    @classmethod
    @abstractmethod
    def from_settings(cls,settings: Settings) -> "LLMClient":
        """Return settings for the class"""
        ...
