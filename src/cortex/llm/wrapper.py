"""Circuit breaker wrapper for LLM clients.

Wraps any ``LLMClient`` with a ``CircuitBreaker``, delegating the ``chat()``
method through the breaker's state machine while forwarding all other methods
via ``__getattr__`` pass-through.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from cortex.llm.base import LLMClient
from cortex.llm.circuit_breaker import CircuitBreaker, CircuitOpenError
from cortex.llm.config import GenerationConfig
from cortex.llm.models import ChatMessage, ChatResult
from cortex.tools.interfaces import ToolDefinition

logger = logging.getLogger(__name__)


class CircuitBreakerLLMClient(LLMClient):
    """Wraps an LLMClient with a CircuitBreaker per module.

    ``chat()`` is routed through the breaker's state machine so that
    repeated failures fast-fail without waiting for a network timeout.

    All other methods (``translate_tools``, ``get_provider_name``, etc.)
    are transparently forwarded to the underlying client — modules using
    this wrapper need no code changes.
    """

    def __init__(
        self,
        client: LLMClient,
        breaker: CircuitBreaker,
        module: str,
    ) -> None:
        self._client = client
        self._breaker = breaker
        self._module = module

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolDefinition] | None = None,
        generation_config: GenerationConfig | None = None,
    ) -> ChatResult:
        """Delegates to ``breaker.call(client.chat(...))``.

        Logs the module name on every breaker event (open rejection
        or call failure).
        """
        coro = self._client.chat(
            messages,
            tools=tools,
            generation_config=generation_config,
        )
        try:
            return await self._breaker.call(coro)
        except CircuitOpenError:
            logger.warning(
                "Circuit breaker OPEN for module '%s'", self._module,
            )
            raise
        except Exception:
            logger.warning(
                "Circuit breaker failure for module '%s'", self._module,
            )
            raise

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolDefinition] | None = None,
        generation_config: GenerationConfig | None = None,
    ) -> AsyncIterator[str | ChatResult]:
        """Forwards streaming directly to the underlying client.

        Streaming is NOT routed through the circuit breaker: the breaker's
        ``call()`` guards a single awaitable resolving to one value, whereas a
        stream is a long-lived async generator. Wrapping it there would give no
        meaningful success/failure signal, so deltas pass straight through.
        """
        async for item in self._client.chat_stream(
            messages,
            tools=tools,
            generation_config=generation_config,
        ):
            yield item

    # -- Explicit pass-through for abstract methods ---------------------------

    def translate_tools(self, tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        return self._client.translate_tools(tools)

    def translate_tool_call(self, raw: dict[str, Any]) -> Any:
        return self._client.translate_tool_call(raw)

    def get_provider_name(self) -> str:
        return self._client.get_provider_name()

    @classmethod
    def from_settings(cls, settings: Any) -> LLMClient:
        """Not supported — use ``LLMClientFactory.create_for_module()``."""
        raise NotImplementedError(
            "Use LLMClientFactory.create_for_module() to create a "
            "CircuitBreakerLLMClient",
        )

    # ------------------------------------------------------------------
    # Generic pass-through
    # ------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        """Pass-through to the underlying client for non-chat methods."""
        return getattr(self._client, name)


__all__ = [
    "CircuitBreakerLLMClient",
]
