"""Abstract base class for LLM clients."""

from abc import ABC, abstractmethod
from typing import Any

from cortex.llm.models import ChatMessage, ChatResult, ToolDefinition
from cortex.llm.config import GenerationConfig


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
