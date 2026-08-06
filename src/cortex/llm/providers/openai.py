"""OpenAI LLM client implementation."""

import logging
from typing import Any

import openai
from openai import AsyncOpenAI

from cortex.config.models import Settings
from cortex.llm.base import LLMClient
from cortex.llm.config import GenerationConfig
from cortex.llm.models import (
    ChatMessage,
    ChatResult,
    Role,
    UsageStats,
)
from cortex.tools.interfaces import ToolCall, ToolDefinition

logger = logging.getLogger(__name__)


class OpenAIClient(LLMClient):
    """
    OpenAI LLM client.

    Supports GPT-4, GPT-4o, GPT-3.5-turbo and function calling.
    """

    def __init__(self, api_key: str, model: str = "gpt-4o"):
        self._api_key = api_key
        self._model = model
        self._client = AsyncOpenAI(api_key=api_key)

    @classmethod
    def from_settings(cls, settings: Settings) -> "OpenAIClient":
        """Create client from settings."""
        return cls(
            api_key=settings.llm_api_key.get_secret_value(),
            model=settings.llm_model,
        )

    def get_provider_name(self) -> str:
        return "openai"

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolDefinition] | None = None,
        generation_config: GenerationConfig | None = None,
    ) -> ChatResult:
        """
        Send chat to OpenAI API.

        Args:
            messages: Conversation history
            tools: Optional tool definitions
            generation_config: Optional generation parameters

        Returns:
            ChatResult from OpenAI
        """
        # Build request kwargs
        request_kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": [self._to_openai_message(m) for m in messages],
        }

        # Add tools if provided
        if tools:
            request_kwargs["tools"] = self.translate_tools(tools)

        # Apply generation config
        if generation_config:
            request_kwargs.update(generation_config.model_dump(exclude_none=True))

        logger.debug(f"OpenAI request: {request_kwargs.get('model')}, {len(messages)} messages")

        try:
            response = await self._client.chat.completions.create(**request_kwargs)

            choice = response.choices[0]
            message = choice.message

            # Parse response
            tool_calls = None
            if message.tool_calls:
                tool_calls = [
                    self.translate_tool_call(tc.model_dump())
                    for tc in message.tool_calls
                ]

            return ChatResult(
                message=ChatMessage(
                    role=Role.ASSISTANT,
                    content=message.content,
                ),
                tool_calls=tool_calls,
                usage=UsageStats(
                    prompt_tokens=response.usage.prompt_tokens,
                    completion_tokens=response.usage.completion_tokens,
                    total_tokens=response.usage.total_tokens,
                ) if response.usage else None,
                model=response.model,
                finish_reason=choice.finish_reason,
            )

        except openai.APIError as e:
            logger.error(f"OpenAI API error: {e}")
            raise

    def translate_tools(self, tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        """
        Convert internal tool schema to OpenAI function format.

        Internal ToolDefinition → OpenAI tools format.
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in tools
        ]

    def translate_tool_call(self, raw: dict[str, Any]) -> ToolCall:
        """
        Convert OpenAI tool call to internal schema.

        OpenAI function call → internal ToolCall.
        """
        import json

        func = raw.get("function", raw)
        args = func.get("arguments", {})

        # Parse JSON string if needed
        if isinstance(args, str):
            args = json.loads(args)

        return ToolCall(
            id=raw.get("id", ""),
            name=func.get("name", ""),
            arguments=args,
        )

    def _to_openai_message(self, message: ChatMessage) -> dict[str, Any]:
        """Convert internal message to OpenAI format."""
        result: dict[str, Any] = {
            "role": message.role.value,
        }

        if message.content:
            result["content"] = message.content

        if message.name:
            result["name"] = message.name

        if message.tool_call_id:
            result["tool_call_id"] = message.tool_call_id

        return result
