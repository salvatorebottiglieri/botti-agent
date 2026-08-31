"""Tests for the LLM Client."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from cortex.llm import (
    ChatMessage,
    GenerationConfig,
    LLMClientFactory,
    Role,
    ToolCall,
    ToolDefinition,
)
from cortex.llm.models import ChatResult
from cortex.llm.providers import OpenAIClient


# -- Streaming test helpers: mimic OpenAI ChatCompletionChunk shapes ----------


def _usage(prompt, completion, total):
    return SimpleNamespace(
        prompt_tokens=prompt, completion_tokens=completion, total_tokens=total
    )


def _tc(index, id=None, name=None, args=None):
    """A streaming tool-call fragment."""
    return SimpleNamespace(
        index=index, id=id, function=SimpleNamespace(name=name, arguments=args)
    )


def _chunk(
    content=None, tool_calls=None, finish=None, usage=None, model="MiniMax-M3",
    reasoning=None,
):
    """A ChatCompletionChunk; usage-only chunks carry no choices. `reasoning`
    populates the out-of-band `reasoning_content` field on the delta."""
    if (
        content is None and tool_calls is None and finish is None
        and reasoning is None and usage is not None
    ):
        choices = []
    else:
        delta = SimpleNamespace(
            content=content, tool_calls=tool_calls, reasoning_content=reasoning
        )
        choices = [SimpleNamespace(delta=delta, finish_reason=finish)]
    return SimpleNamespace(model=model, usage=usage, choices=choices)


async def _drain_stream(agen):
    """Collect str deltas and the terminal ChatResult from chat_stream()."""
    deltas = []
    final = None
    async for item in agen:
        if isinstance(item, ChatResult):
            final = item
        else:
            deltas.append(item)
    return deltas, final


class TestChatMessage:
    """Test cases for ChatMessage."""

    def test_user_message(self):
        """Test creating a user message."""
        msg = ChatMessage(role=Role.USER, content="Hello!")
        assert msg.role == Role.USER
        assert msg.content == "Hello!"

    def test_assistant_message(self):
        """Test creating an assistant message."""
        msg = ChatMessage(role=Role.ASSISTANT, content="Hi there!")
        assert msg.role == Role.ASSISTANT
        assert msg.content == "Hi there!"

    def test_tool_message(self):
        """Test creating a tool result message."""
        msg = ChatMessage(
            role=Role.TOOL,
            content='{"result": "file content"}',
            tool_call_id="call_123",
            name="read_file"
        )
        assert msg.role == Role.TOOL
        assert msg.tool_call_id == "call_123"
        assert msg.name == "read_file"


class TestToolCall:
    """Test cases for ToolCall."""

    def test_tool_call_auto_id(self):
        """Test that tool calls get auto-generated IDs."""
        tc = ToolCall(name="read_file", arguments={"path": "/tmp/test"})
        assert tc.id.startswith("call_")
        assert tc.name == "read_file"
        assert tc.arguments["path"] == "/tmp/test"

    def test_tool_call_custom_id(self):
        """Test tool call with custom ID."""
        tc = ToolCall(id="my_call", name="test", arguments={})
        assert tc.id == "my_call"


class TestToolDefinition:
    """Test cases for ToolDefinition."""

    def test_tool_definition(self):
        """Test creating a tool definition."""
        tool = ToolDefinition(
            name="read_file",
            description="Read contents of a file",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"}
                },
                "required": ["path"]
            }
        )
        assert tool.name == "read_file"
        assert "path" in tool.input_schema["properties"]


class TestGenerationConfig:
    """Test cases for GenerationConfig."""

    def test_default_config(self):
        """Test default configuration."""
        config = GenerationConfig()
        assert config.temperature is None
        assert config.max_tokens is None

    def test_custom_config(self):
        """Test custom configuration."""
        config = GenerationConfig(
            temperature=0.7,
            max_tokens=1000,
            top_p=0.9
        )
        assert config.temperature == 0.7
        assert config.max_tokens == 1000
        assert config.top_p == 0.9

    def test_temperature_bounds(self):
        """Test temperature bounds validation."""
        with pytest.raises(ValueError):
            GenerationConfig(temperature=3.0)  # > 2.0


class TestOpenAIClient:
    """Test cases for OpenAI client."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = MagicMock()
        settings.llm_api_key.get_secret_value.return_value = "test-key"
        settings.llm_model = "gpt-4o"
        settings.llm_base_url = None
        return settings

    def test_from_settings(self, mock_settings):
        """Test creating client from settings."""
        client = OpenAIClient.from_settings(mock_settings)
        assert client._model == "gpt-4o"

    def test_get_provider_name(self):
        """Test provider name."""
        client = OpenAIClient(api_key="test", model="gpt-4o")
        assert client.get_provider_name() == "openai"

    def test_translate_tools(self):
        """Test tool translation to OpenAI format."""
        client = OpenAIClient(api_key="test")
        tools = [
            ToolDefinition(
                name="read_file",
                description="Read a file",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"}
                    }
                }
            )
        ]

        translated = client.translate_tools(tools)

        assert len(translated) == 1
        assert translated[0]["type"] == "function"
        assert translated[0]["function"]["name"] == "read_file"

    def test_translate_tool_call(self):
        """Test tool call translation from OpenAI format."""
        client = OpenAIClient(api_key="test")
        raw = {
            "id": "call_abc123",
            "function": {
                "name": "read_file",
                "arguments": '{"path": "/tmp/test"}'
            }
        }

        tool_call = client.translate_tool_call(raw)

        assert tool_call.id == "call_abc123"
        assert tool_call.name == "read_file"

    def test_to_openai_message_serializes_tool_call_contract(self):
        """Tool messages carry tool_call_id; assistant tool_calls translate to
        the OpenAI wire shape ONLY at the provider boundary."""
        import json

        client = OpenAIClient(api_key="test")

        tool_msg = client._to_openai_message(ChatMessage(
            role=Role.TOOL,
            content="output",
            tool_call_id="call_x",
        ))
        assert tool_msg["role"] == "tool"
        assert tool_msg["tool_call_id"] == "call_x"

        assistant_msg = client._to_openai_message(ChatMessage(
            role=Role.ASSISTANT,
            content="",
            tool_calls=[ToolCall(id="call_x", name="shell", arguments={"command": "echo hi"})],
        ))
        assert assistant_msg["tool_calls"] == [{
            "id": "call_x",
            "type": "function",
            "function": {"name": "shell", "arguments": json.dumps({"command": "echo hi"})},
        }]
        assert "content" not in assistant_msg  # falsy content omitted for tool-call turns

    async def test_chat_stream_text_only(self):
        """Text deltas are yielded in order, then a final ChatResult with the
        joined content and stream usage."""
        from unittest.mock import AsyncMock, patch

        client = OpenAIClient(api_key="test", model="MiniMax-M3")

        async def fake_stream():
            yield _chunk(content="Ciao ")
            yield _chunk(content="mondo", finish="stop")
            yield _chunk(usage=_usage(4, 2, 6))  # usage-only final chunk, no choices

        with patch.object(
            client._client.chat.completions,
            "create",
            new=AsyncMock(return_value=fake_stream()),
        ):
            deltas, final = await _drain_stream(
                client.chat_stream([ChatMessage(role=Role.USER, content="hi")])
            )

        assert deltas == ["Ciao ", "mondo"]
        assert final.message.content == "Ciao mondo"
        assert final.tool_calls is None
        assert final.finish_reason == "stop"
        assert final.usage.total_tokens == 6

    async def test_chat_stream_assembles_fragmented_tool_call(self):
        """Tool-call fragments spread across chunks (by index) are re-assembled
        into a single internal ToolCall with parsed arguments."""
        from unittest.mock import AsyncMock, patch

        client = OpenAIClient(api_key="test", model="MiniMax-M3")

        async def fake_stream():
            yield _chunk(tool_calls=[_tc(0, id="call_1", name="get_", args='{"a"')])
            yield _chunk(tool_calls=[_tc(0, args=":1}")])
            yield _chunk(finish="tool_calls")

        with patch.object(
            client._client.chat.completions,
            "create",
            new=AsyncMock(return_value=fake_stream()),
        ):
            deltas, final = await _drain_stream(
                client.chat_stream([ChatMessage(role=Role.USER, content="hi")])
            )

        assert deltas == []  # no text deltas on a pure tool-call turn
        assert final.message.content is None
        assert final.tool_calls is not None
        assert len(final.tool_calls) == 1
        assert final.tool_calls[0].name == "get_"
        assert final.tool_calls[0].arguments == {"a": 1}
        assert final.finish_reason == "tool_calls"

    async def test_chat_stream_wraps_out_of_band_reasoning_in_think_tags(self):
        """Reasoning delivered via reasoning_content is normalized to an inline
        <think>...</think> block: opened on the first reasoning token, closed
        when the answer content starts."""
        from unittest.mock import AsyncMock, patch

        client = OpenAIClient(api_key="test", model="MiniMax-M3")

        async def fake_stream():
            yield _chunk(reasoning="Let me ")
            yield _chunk(reasoning="think.")
            yield _chunk(content="Hello ")
            yield _chunk(content="world", finish="stop")

        with patch.object(
            client._client.chat.completions,
            "create",
            new=AsyncMock(return_value=fake_stream()),
        ):
            deltas, final = await _drain_stream(
                client.chat_stream([ChatMessage(role=Role.USER, content="hi")])
            )

        # <think> opens, reasoning streams, </think> closes before the answer.
        assert deltas == ["<think>", "Let me ", "think.", "</think>", "Hello ", "world"]
        assert final.message.content == "<think>Let me think.</think>Hello world"
        assert final.finish_reason == "stop"

    async def test_chat_stream_closes_think_when_reasoning_has_no_content(self):
        """Reasoning followed by a tool call (no answer text) still yields a
        balanced <think>...</think> block, closed at end of stream."""
        from unittest.mock import AsyncMock, patch

        client = OpenAIClient(api_key="test", model="MiniMax-M3")

        async def fake_stream():
            yield _chunk(reasoning="I should call a tool.")
            yield _chunk(tool_calls=[_tc(0, id="call_1", name="search", args="{}")])
            yield _chunk(finish="tool_calls")

        with patch.object(
            client._client.chat.completions,
            "create",
            new=AsyncMock(return_value=fake_stream()),
        ):
            deltas, final = await _drain_stream(
                client.chat_stream([ChatMessage(role=Role.USER, content="hi")])
            )

        assert deltas == ["<think>", "I should call a tool.", "</think>"]
        assert final.message.content == "<think>I should call a tool.</think>"
        assert final.tool_calls is not None
        assert final.tool_calls[0].name == "search"

    async def test_chat_stream_leaves_inline_think_untouched(self):
        """A model that already emits inline <think> in content is passed
        through verbatim — no double-wrapping."""
        from unittest.mock import AsyncMock, patch

        client = OpenAIClient(api_key="test", model="MiniMax-M3")

        async def fake_stream():
            yield _chunk(content="<think>inline</think>")
            yield _chunk(content="Answer", finish="stop")

        with patch.object(
            client._client.chat.completions,
            "create",
            new=AsyncMock(return_value=fake_stream()),
        ):
            deltas, final = await _drain_stream(
                client.chat_stream([ChatMessage(role=Role.USER, content="hi")])
            )

        assert deltas == ["<think>inline</think>", "Answer"]
        assert final.message.content == "<think>inline</think>Answer"


class TestLLMClientFactory:
    """Test cases for LLM client factory."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = MagicMock()
        settings.llm_provider = "openai"
        settings.llm_api_key.get_secret_value.return_value = "test-key"
        settings.llm_model = "gpt-4o"
        settings.llm_base_url = None
        return settings

    def test_create_default_provider(self, mock_settings):
        """Test creating client with default provider."""
        factory = LLMClientFactory(mock_settings)
        client = factory.create()
        assert client.get_provider_name() == "openai"

    def test_create_explicit_provider(self, mock_settings):
        """Test creating client with explicit provider."""
        factory = LLMClientFactory(mock_settings)
        client = factory.create(provider="openai")
        assert client.get_provider_name() == "openai"

    def test_unsupported_provider(self, mock_settings):
        """Test that unsupported provider raises error."""
        factory = LLMClientFactory(mock_settings)
        with pytest.raises(ValueError, match="Unsupported LLM provider"):
            factory.create(provider="unknown")

    def test_register_provider(self):
        """Test registering a new provider."""
        class CustomClient:
            def get_provider_name(self):
                return "custom"

        LLMClientFactory.register_provider("custom", CustomClient)

        settings = MagicMock()
        settings.llm_provider = "openai"

        factory = LLMClientFactory(settings)
        # This would fail because CustomClient isn't a proper subclass,
        # but the registration should work
        with pytest.raises(AttributeError):
            factory.create(provider="custom")
