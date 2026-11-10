"""Tests for the LLM Client."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import openai
import pytest

from cortex.llm import (
    ChatMessage,
    GenerationConfig,
    LLMClientFactory,
    Role,
    ToolCall,
    ToolDefinition,
)
from cortex.llm.config import LLMSettings
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
        """LLM settings slice handed to the client."""
        return LLMSettings(api_key="test-key", model="gpt-4o", base_url=None, _env_file=None)

    def test_from_settings(self, mock_settings):
        """Test creating client from the LLM settings slice."""
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


class TestOpenAIClientEmbed:
    """``OpenAIClient.embed()`` — ADR-0008 / issue #30.

    Witness values are deliberately non-default (``custom-embed-v2`` / 8
    dimensions): an implementation that hardcodes ``text-embedding-3-small``
    or 1536 fails even though it would pass a default-valued assertion.
    """

    def _client(self, *, dimensions: int = 8) -> OpenAIClient:
        return OpenAIClient(
            api_key="test",
            embedding_model="custom-embed-v2",
            embedding_dimensions=dimensions,
        )

    @staticmethod
    def _response(vector):
        return SimpleNamespace(data=[SimpleNamespace(embedding=vector)])

    async def test_embed_calls_configured_model_and_dimensions(self):
        """The embeddings endpoint receives the configured model, input text
        and dimensions — not a hardcoded default."""
        client = self._client()
        create = AsyncMock(return_value=self._response([0.5] * 8))

        with patch.object(client._client.embeddings, "create", new=create):
            result = await client.embed("hello world")

        create.assert_awaited_once_with(
            model="custom-embed-v2",
            input="hello world",
            dimensions=8,
        )
        assert result == [0.5] * 8

    async def test_embed_returns_list_of_floats(self):
        """Provider values (which may arrive as ints) become ``list[float]``."""
        client = self._client(dimensions=2)
        create = AsyncMock(return_value=self._response([1, 2]))

        with patch.object(client._client.embeddings, "create", new=create):
            result = await client.embed("text")

        assert isinstance(result, list)
        assert result == [1.0, 2.0]
        assert all(isinstance(value, float) for value in result)

    async def test_embed_rejects_vector_with_wrong_dimensions(self):
        """A vector shorter than the configured dimension raises: silently
        truncating or padding would store a vector the pgvector column cannot
        accept (issue #31)."""
        client = self._client(dimensions=3)
        create = AsyncMock(return_value=self._response([0.1, 0.2]))

        with patch.object(client._client.embeddings, "create", new=create):
            with pytest.raises(ValueError, match=r"expected 3, got 2"):
                await client.embed("text")

    async def test_embed_propagates_api_error(self):
        """A provider failure surfaces as ``openai.APIError`` unchanged, so the
        caller can fall back to ILIKE (issue #31)."""
        client = self._client()
        error = openai.APIConnectionError(
            request=httpx.Request("POST", "https://example.test/v1/embeddings")
        )
        create = AsyncMock(side_effect=error)

        with patch.object(client._client.embeddings, "create", new=create):
            with pytest.raises(openai.APIError) as raised:
                await client.embed("text")

        assert raised.value is error

    def test_from_settings_passes_embedding_config(self):
        """``from_settings`` forwards the embedding slice of ``LLMSettings``."""
        settings = LLMSettings(
            api_key="test-key",
            model="gpt-4o",
            embedding_model="custom-embed-v2",
            embedding_dimensions=8,
            _env_file=None,
        )

        client = OpenAIClient.from_settings(settings)

        assert client._embedding_model == "custom-embed-v2"
        assert client._embedding_dimensions == 8


class TestLLMClientFactory:
    """Test cases for LLM client factory."""

    @pytest.fixture
    def mock_settings(self):
        """LLM settings slice handed to the factory."""
        return LLMSettings(
            provider="openai", api_key="test-key", model="gpt-4o", base_url=None, _env_file=None
        )

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

    def test_created_client_exposes_embed(self, mock_settings):
        """Embedding generation is part of the factory's client contract."""
        client = LLMClientFactory(mock_settings).create()
        assert hasattr(client, "embed")

    def test_register_provider(self):
        """Test registering a new provider."""
        class CustomClient:
            def get_provider_name(self):
                return "custom"

        LLMClientFactory.register_provider("custom", CustomClient)

        settings = LLMSettings(api_key="test-key", _env_file=None)

        factory = LLMClientFactory(settings)
        # This would fail because CustomClient isn't a proper subclass,
        # but the registration should work
        with pytest.raises(AttributeError):
            factory.create(provider="custom")
