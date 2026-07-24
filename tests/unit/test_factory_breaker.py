"""Tests for CircuitBreaker wiring into LLMFactory.

Verifies:
- Factory.create_for_module() returns CircuitBreakerLLMClient
- chat() calls go through breaker.call()
- CircuitBreaker intercepts failures
- Module name logged on breaker events
- __getattr__ pass-through for non-chat methods
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from cortex.config.models import Settings
from cortex.llm.base import LLMClient
from cortex.llm.circuit_breaker import CircuitBreaker, CircuitOpenError
from cortex.llm.factory import LLMClientFactory
from cortex.llm.models import ChatMessage, ChatResult
from cortex.llm.wrapper import CircuitBreakerLLMClient


class TestFactoryBreaker:
    """Test that LLMFactory wires CircuitBreaker into module clients."""

    # ------------------------------------------------------------------
    # Factory integration
    # ------------------------------------------------------------------

    def test_create_for_module_returns_wrapped_client(self):
        """Factory.create_for_module() returns a CircuitBreakerLLMClient."""
        settings = Settings(
            llm_api_key="test-key",
        )
        factory = LLMClientFactory(settings)
        client = factory.create_for_module("execution")
        assert isinstance(client, CircuitBreakerLLMClient)

    def test_create_for_module_uses_custom_provider(self):
        """Factory.create_for_module() respects explicit provider."""
        settings = Settings(
            llm_api_key="test-key",
        )
        factory = LLMClientFactory(settings)
        client = factory.create_for_module("memory", provider="openai")
        assert isinstance(client, CircuitBreakerLLMClient)
        assert client.get_provider_name() == "openai"

    def test_create_for_module_configures_breaker_from_settings(self):
        """Breaker thresholds come from Settings."""
        settings = Settings(
            llm_api_key="test-key",
            circuit_breaker_threshold=10,
            circuit_breaker_timeout=60.0,
            circuit_breaker_half_open_successes=5,
        )
        factory = LLMClientFactory(settings)
        client = factory.create_for_module("learning")
        assert client._breaker.failure_threshold == 10
        assert client._breaker.recovery_timeout == 60.0
        assert client._breaker.half_open_successes == 5

    # ------------------------------------------------------------------
    # CircuitBreakerLLMClient.chat() delegation
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_wrapped_client_delegates_chat_via_breaker(self):
        """Wrapped client's chat() goes through breaker.call()."""
        mock_client = AsyncMock(spec=LLMClient)
        expected = ChatResult(
            message=ChatMessage(role="assistant", content="ok"),
        )
        mock_client.chat.return_value = expected

        mock_breaker = AsyncMock(spec=CircuitBreaker)
        mock_breaker.call.return_value = expected

        wrapper = CircuitBreakerLLMClient(mock_client, mock_breaker, "test_module")
        result = await wrapper.chat([ChatMessage(role="user", content="hello")])

        assert result is expected
        mock_breaker.call.assert_awaited_once()
        mock_client.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_wrapped_client_passes_tools_and_config(self):
        """chat() forwards tools and generation_config to the base client."""
        mock_client = AsyncMock(spec=LLMClient)
        mock_client.chat.return_value = ChatResult(
            message=ChatMessage(role="assistant", content="ok"),
        )
        mock_breaker = AsyncMock(spec=CircuitBreaker)
        mock_breaker.call.return_value = mock_client.chat.return_value

        wrapper = CircuitBreakerLLMClient(mock_client, mock_breaker, "test")

        tools = MagicMock()
        gen_config = MagicMock()
        await wrapper.chat(
            [ChatMessage(role="user", content="hi")],
            tools=tools,
            generation_config=gen_config,
        )

        mock_client.chat.assert_called_once_with(
            [ChatMessage(role="user", content="hi")],
            tools=tools,
            generation_config=gen_config,
        )

    # ------------------------------------------------------------------
    # Circuit breaker failure interception
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_circuit_breaker_intercepts_failures(self):
        """Circuit breaker trips open after repeated failures."""
        mock_client = AsyncMock(spec=LLMClient)
        mock_client.chat.side_effect = Exception("API error")

        breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=60.0)
        wrapper = CircuitBreakerLLMClient(mock_client, breaker, "test_module")

        # First failure — tracked, still CLOSED
        with pytest.raises(Exception, match="API error"):
            await wrapper.chat([ChatMessage(role="user", content="hi")])

        # Second failure — trips OPEN
        with pytest.raises(Exception, match="API error"):
            await wrapper.chat([ChatMessage(role="user", content="hi")])

        # Third call — circuit is OPEN, should raise CircuitOpenError
        with pytest.raises(CircuitOpenError):
            await wrapper.chat([ChatMessage(role="user", content="hi")])

    # ------------------------------------------------------------------
    # Module name logging
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_module_name_logged_on_circuit_open(self, caplog):
        """Module name logged when circuit is OPEN."""
        mock_client = AsyncMock(spec=LLMClient)
        mock_client.chat.side_effect = Exception("API error")

        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=60.0)
        wrapper = CircuitBreakerLLMClient(mock_client, breaker, "execution")

        with caplog.at_level(logging.WARNING):
            with pytest.raises(Exception):
                await wrapper.chat([ChatMessage(role="user", content="hi")])

        assert any("execution" in record.getMessage() for record in caplog.records)

    @pytest.mark.asyncio
    async def test_module_name_logged_on_failure(self, caplog):
        """Module name logged on circuit breaker failure (CLOSED->OPEN trip)."""
        mock_client = AsyncMock(spec=LLMClient)
        mock_client.chat.side_effect = Exception("boom")

        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=60.0)
        wrapper = CircuitBreakerLLMClient(mock_client, breaker, "memory")

        with caplog.at_level(logging.WARNING):
            with pytest.raises(Exception):
                await wrapper.chat([ChatMessage(role="user", content="hi")])

        # Should have logged "memory" in a warning
        relevant = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING and "memory" in r.getMessage()
        ]
        assert len(relevant) >= 1

    # ------------------------------------------------------------------
    # __getattr__ pass-through
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_getattr_pass_through(self):
        """Non-chat methods pass through to underlying client."""
        mock_client = MagicMock()
        mock_client.custom_method.return_value = "custom_result"

        breaker = CircuitBreaker()
        wrapper = CircuitBreakerLLMClient(mock_client, breaker, "test_module")

        result = wrapper.custom_method()
        assert result == "custom_result"
        mock_client.custom_method.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_getattr_delegates_translate_tools(self):
        """translate_tools() is delegated to underlying client."""
        mock_client = AsyncMock(spec=LLMClient)
        tool_defs = [MagicMock()]
        mock_client.translate_tools.return_value = [{"type": "function"}]

        breaker = CircuitBreaker()
        wrapper = CircuitBreakerLLMClient(mock_client, breaker, "test")

        result = wrapper.translate_tools(tool_defs)
        assert result == [{"type": "function"}]
        mock_client.translate_tools.assert_called_once_with(tool_defs)

    @pytest.mark.asyncio
    async def test_getattr_raises_attribute_error(self):
        """__getattr__ raises AttributeError for missing attributes."""
        mock_client = MagicMock(spec=LLMClient)
        # Remove chat because we'll use the explicit method
        breaker = CircuitBreaker()
        wrapper = CircuitBreakerLLMClient(mock_client, breaker, "test")

        # Use a non-existent attribute
        mock_client.side_effect = None
        with pytest.raises(AttributeError):
            wrapper.nonexistent_attr

    @pytest.mark.asyncio
    async def test_create_execution_learning_memory_use_separate_breakers(self):
        """Each module gets its own CircuitBreaker instance."""
        settings = Settings(llm_api_key="test-key")
        factory = LLMClientFactory(settings)

        exec_client = factory.create_for_module("execution")
        mem_client = factory.create_for_module("memory")
        learn_client = factory.create_for_module("learning")

        assert exec_client._breaker is not mem_client._breaker
        assert exec_client._breaker is not learn_client._breaker
        assert mem_client._breaker is not learn_client._breaker

    @pytest.mark.asyncio
    async def test_from_settings_raises_not_implemented(self):
        """from_settings is not supported on CircuitBreakerLLMClient."""
        with pytest.raises(NotImplementedError):
            CircuitBreakerLLMClient.from_settings(MagicMock())
