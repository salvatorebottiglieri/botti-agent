"""OpenAI LLM client implementation."""

import logging
from collections.abc import AsyncIterator
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


def _reasoning_delta(delta: Any) -> str | None:
    """Extract a reasoning-token delta from a streaming chunk.

    Reasoning models that deliver chain-of-thought out-of-band use a dedicated
    field (``reasoning_content`` on DeepSeek-R1, ``reasoning`` on some others)
    rather than inline ``<think>`` tags. The typed SDK delta may expose it as an
    attribute or only via ``model_extra``; both are checked. Returns None for
    models that put reasoning inline in ``content`` (nothing to normalize).
    """
    for name in ("reasoning_content", "reasoning"):
        val = getattr(delta, name, None)
        if val is None:
            extra = getattr(delta, "model_extra", None)
            if extra:
                val = extra.get(name)
        if val:
            return val
    return None


class OpenAIClient(LLMClient):
    """
    OpenAI LLM client.

    Supports GPT-4, GPT-4o, GPT-3.5-turbo and function calling.
    """

    def __init__(self, api_key: str, model: str = "gpt-4o", base_url: str | None = None):
        self._api_key = api_key
        self._model = model
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    @classmethod
    def from_settings(cls, settings: Settings) -> "OpenAIClient":
        """Create client from settings."""
        return cls(
            api_key=settings.llm_api_key.get_secret_value(),
            model=settings.llm_model,
            base_url=settings.llm_base_url,
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

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolDefinition] | None = None,
        generation_config: GenerationConfig | None = None,
    ) -> AsyncIterator[str | ChatResult]:
        """
        Streaming variant of ``chat()``.

        Yields text deltas (``str``) as they arrive from the provider, then
        yields a single final ``ChatResult`` as the terminal item — content,
        tool calls and usage assembled from the stream. Callers iterate and
        collect the ``str`` deltas; the only non-``str`` item, yielded last,
        is the complete ``ChatResult``.

        The same request as ``chat()`` is sent with ``stream=True``; tool-call
        fragments (spread across chunks by ``index``) are accumulated and
        re-assembled into internal ``ToolCall`` objects at the end.

        Out-of-band reasoning (a separate ``reasoning_content`` field, not inline
        ``<think>`` tags) is normalized to the inline convention: it is wrapped in
        a single ``<think>...</think>`` block and emitted as content deltas, so
        every downstream consumer — and the UI's reasoning toggle — treats both
        reasoning-delivery styles identically.
        """
        request_kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": [self._to_openai_message(m) for m in messages],
            "stream": True,
            # Ask for usage on the final chunk (OpenAI-compatible providers).
            "stream_options": {"include_usage": True},
        }

        if tools:
            request_kwargs["tools"] = self.translate_tools(tools)

        if generation_config:
            request_kwargs.update(generation_config.model_dump(exclude_none=True))

        logger.debug(
            f"OpenAI stream request: {request_kwargs.get('model')}, {len(messages)} messages"
        )

        content_parts: list[str] = []
        # index -> {"id", "name", "arguments"} accumulated across chunks
        tool_fragments: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        model_name: str | None = None
        usage: UsageStats | None = None
        think_open = False  # True while inside a synthesized <think> block

        try:
            stream = await self._client.chat.completions.create(**request_kwargs)

            async for chunk in stream:
                if chunk.model:
                    model_name = chunk.model
                if chunk.usage:
                    usage = UsageStats(
                        prompt_tokens=chunk.usage.prompt_tokens,
                        completion_tokens=chunk.usage.completion_tokens,
                        total_tokens=chunk.usage.total_tokens,
                    )
                # The usage-only final chunk carries no choices.
                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                if choice.finish_reason:
                    finish_reason = choice.finish_reason

                delta = choice.delta

                # Out-of-band reasoning: open a <think> block on first token.
                reasoning = _reasoning_delta(delta)
                if reasoning:
                    if not think_open:
                        content_parts.append("<think>")
                        yield "<think>"
                        think_open = True
                    content_parts.append(reasoning)
                    yield reasoning

                if delta.content:
                    # Answer text has started — close any open reasoning block.
                    if think_open:
                        content_parts.append("</think>")
                        yield "</think>"
                        think_open = False
                    content_parts.append(delta.content)
                    yield delta.content

                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        frag = tool_fragments.setdefault(
                            tc.index, {"id": "", "name": "", "arguments": ""}
                        )
                        if tc.id:
                            frag["id"] = tc.id
                        if tc.function and tc.function.name:
                            frag["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            frag["arguments"] += tc.function.arguments

        except openai.APIError as e:
            logger.error(f"OpenAI streaming error: {e}")
            raise

        # Reasoning with no trailing content (e.g. reasoning then a tool call, or
        # reasoning-only): close the block so the <think> tag is always balanced.
        if think_open:
            content_parts.append("</think>")
            yield "</think>"
            think_open = False

        tool_calls = None
        if tool_fragments:
            tool_calls = [
                self.translate_tool_call(
                    {
                        "id": frag["id"],
                        "function": {
                            "name": frag["name"],
                            "arguments": frag["arguments"],
                        },
                    }
                )
                for _, frag in sorted(tool_fragments.items())
            ]

        content = "".join(content_parts) or None
        yield ChatResult(
            message=ChatMessage(role=Role.ASSISTANT, content=content),
            tool_calls=tool_calls,
            usage=usage,
            model=model_name,
            finish_reason=finish_reason,
        )

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

        if message.tool_calls:
            result["tool_calls"] = self.translate_tool_calls(message.tool_calls)

        return result

    def translate_tool_calls(self, tool_calls: list[ToolCall]) -> list[dict[str, Any]]:
        """
        Translate internal ToolCall objects to the OpenAI wire shape.

        The wire shape (type/function nesting, JSON-string arguments) is a
        provider detail — it never travels above this module.
        """
        import json

        return [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                },
            }
            for call in tool_calls
        ]
