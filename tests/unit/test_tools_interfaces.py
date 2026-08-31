"""Tests for tool interfaces and models."""

from uuid import uuid4

import pytest

from cortex.tools.interfaces import (
    Tool,
    ToolCall,
    ToolDefinition,
    ToolErrorSeverity,
    ToolResult,
)


class TestToolCall:
    """Tests for ToolCall dataclass."""

    def test_auto_id_generation(self):
        """ToolCall auto-generates an ID if not provided."""
        call = ToolCall(name="test", arguments={})
        assert call.id is not None
        assert len(call.id) > 0

    def test_custom_id(self):
        """ToolCall accepts custom ID."""
        custom_id = str(uuid4())
        call = ToolCall(id=custom_id, name="test", arguments={})
        assert call.id == custom_id

    def test_with_arguments(self):
        """ToolCall stores arguments correctly."""
        args = {"path": "/tmp/test", "limit": 10}
        call = ToolCall(name="read", arguments=args)
        assert call.arguments == args
        assert call.arguments["path"] == "/tmp/test"

    def test_empty_arguments(self):
        """ToolCall handles empty arguments."""
        call = ToolCall(name="test", arguments={})
        assert call.arguments == {}


class TestToolResult:
    """Tests for ToolResult dataclass."""

    def test_successful_result(self):
        """ToolResult can represent success."""
        result = ToolResult(
            tool_call_id="call-123",
            tool_name="read",
            success=True,
            output="file content here"
        )
        assert result.success is True
        assert result.output == "file content here"
        assert result.error is None

    def test_failed_result(self):
        """ToolResult can represent failure."""
        result = ToolResult(
            tool_call_id="call-123",
            tool_name="read",
            success=False,
            error="File not found"
        )
        assert result.success is False
        assert result.error == "File not found"
        assert result.output is None

    def test_result_with_metadata(self):
        """ToolResult includes metadata."""
        result = ToolResult(
            tool_call_id="call-123",
            tool_name="read",
            success=True,
            output="ok",
            execution_time_ms=150.5,
            metadata={"size": 1234}
        )
        assert result.execution_time_ms == 150.5
        assert result.metadata["size"] == 1234

    def test_result_with_severity(self):
        """ToolResult includes error severity."""
        result = ToolResult(
            tool_call_id="call-123",
            tool_name="shell",
            success=False,
            error="Timeout",
            error_severity=ToolErrorSeverity.WARNING
        )
        assert result.error_severity == ToolErrorSeverity.WARNING

    def test_control_defaults_to_none(self):
        """Ordinary tool results carry no control signal."""
        result = ToolResult(tool_call_id="call-123", tool_name="read", success=True)
        assert result.control is None

    def test_control_signal_can_be_set(self):
        """A tool can flag a loop-interrupting control signal (e.g. ask_user)."""
        result = ToolResult(
            tool_call_id="call-123",
            tool_name="ask_user",
            success=True,
            output="Which file?",
            control="ask_user",
        )
        assert result.control == "ask_user"


class TestToolDefinition:
    """Tests for ToolDefinition dataclass."""

    def test_basic_definition(self):
        """ToolDefinition stores basic tool info."""
        definition = ToolDefinition(
            name="read_file",
            description="Read a file from disk",
            input_schema={"type": "object", "properties": {}}
        )
        assert definition.name == "read_file"
        assert definition.category == "general"
        assert definition.tags == []

    def test_full_definition(self):
        """ToolDefinition stores all properties."""
        definition = ToolDefinition(
            name="custom_tool",
            description="A custom tool",
            input_schema={"type": "object"},
            output_schema={"type": "string"},
            category="special",
            tags=["custom", "demo"]
        )
        assert definition.category == "special"
        assert definition.tags == ["custom", "demo"]
        assert definition.output_schema == {"type": "string"}


class TestToolErrorSeverity:
    """Tests for ToolErrorSeverity enum."""

    def test_severity_values(self):
        """Severity has expected values."""
        assert ToolErrorSeverity.WARNING.value == "warning"
        assert ToolErrorSeverity.ERROR.value == "error"
        assert ToolErrorSeverity.CRITICAL.value == "critical"


class TestToolInterface:
    """Tests for Tool abstract base class."""

    def test_tool_is_abstract(self):
        """Tool cannot be instantiated directly."""
        with pytest.raises(TypeError):
            Tool()

    def test_tool_subclass_must_implement_properties(self):
        """Tool subclass must implement required properties."""

        class IncompleteTool(Tool):
            pass  # Missing name, description, execute

        with pytest.raises(TypeError):
            IncompleteTool()

    def test_tool_subclass_complete(self):
        """Tool subclass with all required methods works."""

        class MyTool(Tool):
            @property
            def name(self) -> str:
                return "my_tool"

            @property
            def description(self) -> str:
                return "A test tool"

            async def execute(self, arguments):
                return ToolResult(
                    tool_call_id="",
                    tool_name=self.name,
                    success=True,
                    output="done"
                )

        tool = MyTool()
        assert tool.name == "my_tool"
        assert tool.timeout_seconds == 60  # default
        assert tool.category == "general"
        assert tool.idempotent is False  # default
