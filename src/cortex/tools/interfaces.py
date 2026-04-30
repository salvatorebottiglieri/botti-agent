"""Tool registry interfaces and models."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4


class ToolErrorSeverity(Enum):
    """Severity level for tool errors."""

    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class ToolDefinition:
    """Definition of a tool for LLM function calling."""

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    category: str = "general"
    tags: list[str] = field(default_factory=list)


@dataclass
class ToolResult:
    """Result from executing a tool."""

    tool_call_id: str
    tool_name: str
    success: bool
    output: str | None = None
    error: str | None = None
    error_severity: ToolErrorSeverity | None = None
    execution_time_ms: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCall:
    """A call to a tool with arguments."""

    id: str = field(default_factory=lambda: str(uuid4()))
    name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class Tool(ABC):
    """
    Abstract base class for all tools.

    Tools are the primary mechanism for the agent to interact with the world.
    Each tool has a name, description, and JSON Schema for arguments.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique name for this tool."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of what this tool does."""
        ...

    @property
    def input_schema(self) -> dict[str, Any]:
        """
        JSON Schema for tool arguments.
        Override to customize schema generation.
        """
        return {"type": "object", "properties": {}, "required": []}

    @property
    def output_schema(self) -> dict[str, Any] | None:
        """JSON Schema for tool output. Optional."""
        return None

    @property
    def category(self) -> str:
        """Category for grouping tools."""
        return "general"

    @property
    def tags(self) -> list[str]:
        """Tags for discovery and filtering."""
        return []

    @property
    def idempotent(self) -> bool:
        """Whether calling this tool multiple times with same args is safe."""
        return False

    @property
    def timeout_seconds(self) -> int:
        """Default timeout for tool execution."""
        return 60

    @abstractmethod
    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        """
        Execute the tool with validated arguments.

        Args:
            arguments: Validated arguments matching input_schema

        Returns:
            ToolResult with output or error
        """
        ...

    def to_definition(self) -> ToolDefinition:
        """Convert tool to a definable schema for LLM."""
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
            output_schema=self.output_schema,
            category=self.category,
            tags=self.tags,
        )


class ToolRegistry(ABC):
    """
    Interface for tool registration and discovery.

    The registry maintains a collection of available tools and provides
    methods for registration, lookup, and listing.
    """

    @abstractmethod
    def register(self, tool: Tool) -> None:
        """
        Register a tool.

        Args:
            tool: Tool instance to register

        Raises:
            ValueError: If a tool with the same name is already registered
        """
        ...

    @abstractmethod
    def unregister(self, name: str) -> bool:
        """
        Unregister a tool by name.

        Returns:
            True if tool was removed, False if not found
        """
        ...

    @abstractmethod
    def get(self, name: str) -> Tool | None:
        """
        Get a tool by name.

        Args:
            name: Tool name

        Returns:
            Tool instance or None if not found
        """
        ...

    @abstractmethod
    def list_all(self) -> list[Tool]:
        """Get all registered tools."""
        ...

    @abstractmethod
    def list_by_category(self, category: str) -> list[Tool]:
        """Get tools in a specific category."""
        ...

    @abstractmethod
    def get_schemas(self) -> list[ToolDefinition]:
        """Get schemas for all tools (for LLM function calling)."""
        ...

    @abstractmethod
    def search(self, query: str) -> list[Tool]:
        """
        Search tools by name, description, or tags.

        Args:
            query: Search query

        Returns:
            Matching tools
        """
        ...


class ToolExecutor(ABC):
    """
    Interface for executing tools.

    The executor wraps tool execution with error handling, timeouts,
    and event emission.
    """

    @abstractmethod
    async def execute(self, tool_call: ToolCall, *, timeout: int | None = None) -> ToolResult:
        """
        Execute a tool call.

        Args:
            tool_call: ToolCall with name and arguments
            timeout: Optional timeout override in seconds

        Returns:
            ToolResult with output or error
        """
        ...

    @abstractmethod
    async def execute_many(
        self, tool_calls: list[ToolCall], *, timeout: int | None = None
    ) -> list[ToolResult]:
        """
        Execute multiple tool calls.

        Args:
            tool_calls: List of tool calls
            timeout: Optional timeout per tool

        Returns:
            List of ToolResults in same order as calls
        """
        ...
