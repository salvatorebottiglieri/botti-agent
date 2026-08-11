"""Tests for the LLM Client."""

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
from cortex.llm.providers import OpenAIClient


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
