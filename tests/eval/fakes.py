"""Deterministic fake LLM client for eval harness tests.

Lets harness tests drive the real AgentLoop (real Reasoner, real meta
tools) without ever hitting a real API: each ``chat()`` call pops the next
scripted :class:`~cortex.llm.models.ChatResult` off a queue.
"""

from __future__ import annotations

from typing import Any

from cortex.config.models import Settings
from cortex.eval.judge import DEFAULT_DIMENSION_ORDER
from cortex.llm.base import LLMClient
from cortex.llm.config import GenerationConfig
from cortex.llm.models import ChatMessage, ChatResult, Role, UsageStats
from cortex.tools.interfaces import ToolCall, ToolDefinition


class ScriptedLLMClient(LLMClient):
    """Returns scripted responses in order, one per ``chat()`` call."""

    def __init__(self, responses: list[ChatResult] | None = None) -> None:
        self._responses: list[ChatResult] = list(responses or [])
        self.calls: list[list[ChatMessage]] = []

    def queue_tool_call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        usage: UsageStats | None = None,
    ) -> None:
        """Queue a response that tells the loop to call a tool."""
        self._responses.append(
            ChatResult(
                message=ChatMessage(role=Role.ASSISTANT, content=""),
                tool_calls=[ToolCall(name=name, arguments=arguments)],
                usage=usage,
            )
        )

    def queue_text(self, text: str, *, usage: UsageStats | None = None) -> None:
        """Queue a response that ends the loop with the given text."""
        self._responses.append(
            ChatResult(
                message=ChatMessage(role=Role.ASSISTANT, content=text),
                usage=usage,
            )
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

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolDefinition] | None = None,
        generation_config: GenerationConfig | None = None,
    ):
        """Streaming mirror of ``chat``: emits the scripted response's text as a
        single delta, then the ``ChatResult`` itself as the terminal item."""
        self.calls.append(list(messages))
        if not self._responses:
            raise AssertionError("ScriptedLLMClient ran out of scripted responses")
        result = self._responses.pop(0)
        content = result.message.content if result.message else None
        if content:
            yield content
        yield result

    def translate_tools(self, tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        return [{"name": tool.name} for tool in tools]

    def translate_tool_call(self, raw: dict[str, Any]) -> ToolCall:
        return ToolCall(**raw)

    def get_provider_name(self) -> str:
        return "scripted"

    @classmethod
    def from_settings(cls, settings: Settings) -> LLMClient:
        return cls()


def scripted_judge_client(
    failed_task_count: int,
    scores: dict[str, int] | None = None,
    usage_per_call: UsageStats | None = None,
) -> ScriptedLLMClient:
    """A judge client serving consistent order-swap form-fill verdicts.

    Each failed task consumes two responses — the forward and reversed
    passes of ``TrajectoryJudge.judge_with_order_swap`` — with identical
    scores, so every verdict is consistent. ``scores`` maps dimension names
    to Likert scores (default: 3 on every dimension). ``usage_per_call``
    attaches a :class:`UsageStats` payload to every queued response so
    tests can assert judge cost accumulation.
    """
    scores = scores or {dim.value: 3 for dim in DEFAULT_DIMENSION_ORDER}
    form = "\n".join(f"{dim.value}: {scores[dim.value]}" for dim in DEFAULT_DIMENSION_ORDER)

    client = ScriptedLLMClient()
    for _ in range(failed_task_count):
        client.queue_text(form, usage=usage_per_call)
        client.queue_text(form, usage=usage_per_call)
    return client
