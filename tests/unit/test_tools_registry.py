"""Tests for tool registry."""

import pytest

from cortex.tools.interfaces import Tool, ToolResult
from cortex.tools.registry import (
    InMemoryToolRegistry,
    ToolAlreadyRegisteredError,
    ToolRegistrar,
)


class MockTool(Tool):
    """Mock tool for testing."""

    def __init__(self, name: str = "mock_tool", category: str = "test"):
        self._name = name
        self._category = category

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"A mock tool named {self._name}"

    @property
    def category(self) -> str:
        return self._category

    async def execute(self, arguments):
        return ToolResult(
            tool_call_id="",
            tool_name=self.name,
            success=True,
            output="mocked"
        )


class TestInMemoryToolRegistry:
    """Tests for InMemoryToolRegistry."""

    def test_empty_registry(self):
        """New registry has no tools."""
        registry = InMemoryToolRegistry()
        assert registry.list_all() == []
        assert registry.tool_count == 0

    def test_register_tool(self):
        """Tools can be registered."""
        registry = InMemoryToolRegistry()
        tool = MockTool("test_tool")
        registry.register(tool)

        assert registry.tool_count == 1
        assert registry.get("test_tool") is tool

    def test_register_duplicate_raises(self):
        """Registering same tool name raises error."""
        registry = InMemoryToolRegistry()
        tool = MockTool("test_tool")
        registry.register(tool)

        with pytest.raises(ToolAlreadyRegisteredError):
            registry.register(tool)

    def test_unregister_tool(self):
        """Tools can be unregistered."""
        registry = InMemoryToolRegistry()
        tool = MockTool("test_tool")
        registry.register(tool)

        assert registry.unregister("test_tool") is True
        assert registry.get("test_tool") is None
        assert registry.tool_count == 0

    def test_unregister_nonexistent(self):
        """Unregistering nonexistent tool returns False."""
        registry = InMemoryToolRegistry()
        assert registry.unregister("nonexistent") is False

    def test_get_tool(self):
        """Get returns registered tool."""
        registry = InMemoryToolRegistry()
        tool = MockTool("test_tool")
        registry.register(tool)

        assert registry.get("test_tool") is tool
        assert registry.get("nonexistent") is None

    def test_list_all(self):
        """List all returns all tools."""
        registry = InMemoryToolRegistry()
        registry.register(MockTool("tool1"))
        registry.register(MockTool("tool2"))

        tools = registry.list_all()
        assert len(tools) == 2
        tool_names = {t.name for t in tools}
        assert tool_names == {"tool1", "tool2"}

    def test_list_by_category(self):
        """List by category filters correctly."""
        registry = InMemoryToolRegistry()
        registry.register(MockTool("tool1", "alpha"))
        registry.register(MockTool("tool2", "beta"))
        registry.register(MockTool("tool3", "alpha"))

        alpha_tools = registry.list_by_category("alpha")
        assert len(alpha_tools) == 2
        assert all(t.category == "alpha" for t in alpha_tools)

        beta_tools = registry.list_by_category("beta")
        assert len(beta_tools) == 1

    def test_get_schemas(self):
        """Get schemas returns definitions."""
        registry = InMemoryToolRegistry()
        tool = MockTool("test_tool")
        registry.register(tool)

        schemas = registry.get_schemas()
        assert len(schemas) == 1
        assert schemas[0].name == "test_tool"

    def test_search_by_name(self):
        """Search finds by name."""
        registry = InMemoryToolRegistry()
        registry.register(MockTool("file_read"))
        registry.register(MockTool("file_write"))
        registry.register(MockTool("shell"))

        results = registry.search("file")
        assert len(results) == 2

    def test_search_by_description(self):
        """Search finds by description."""
        registry = InMemoryToolRegistry()
        registry.register(MockTool("file_read"))
        registry.register(MockTool("shell"))

        # Search for "mock" which is in the MockTool description
        results = registry.search("mock")
        assert len(results) == 2  # Both tools have "mock" in description

    def test_search_empty_query(self):
        """Empty query returns all tools."""
        registry = InMemoryToolRegistry()
        registry.register(MockTool("tool1"))
        registry.register(MockTool("tool2"))

        results = registry.search("")
        assert len(results) == 2

    def test_search_case_insensitive(self):
        """Search is case-insensitive."""
        registry = InMemoryToolRegistry()
        registry.register(MockTool("FileRead"))

        results = registry.search("fileread")
        assert len(results) == 1

    def test_categories_property(self):
        """Categories returns all unique categories."""
        registry = InMemoryToolRegistry()
        registry.register(MockTool("tool1", "alpha"))
        registry.register(MockTool("tool2", "beta"))

        assert set(registry.categories) == {"alpha", "beta"}

    def test_copy(self):
        """Copy creates independent registry."""
        registry = InMemoryToolRegistry()
        registry.register(MockTool("tool1"))

        new_registry = registry.copy()
        assert new_registry.tool_count == 1

        # Changes to copy don't affect original
        new_registry.unregister("tool1")
        assert registry.tool_count == 1


class TestToolRegistrar:
    """Tests for ToolRegistrar helper class."""

    def test_chained_registration(self):
        """Registrar supports chaining."""
        registry = InMemoryToolRegistry()
        registrar = ToolRegistrar(registry)

        registrar.register(MockTool("tool1")).register(MockTool("tool2"))

        assert registry.tool_count == 2

    def test_register_many(self):
        """Register many registers multiple tools."""
        registry = InMemoryToolRegistry()
        registrar = ToolRegistrar(registry)

        tools = [MockTool("tool1"), MockTool("tool2"), MockTool("tool3")]
        registrar.register_many(tools)

        assert registry.tool_count == 3

    def test_registered_list(self):
        """Registered property tracks names."""
        registry = InMemoryToolRegistry()
        registrar = ToolRegistrar(registry)

        registrar.register(MockTool("tool1")).register(MockTool("tool2"))

        assert set(registrar.registered) == {"tool1", "tool2"}
