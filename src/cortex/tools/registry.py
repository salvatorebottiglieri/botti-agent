"""Tool registry implementation."""

from typing import Self

from cortex.tools.interfaces import Tool, ToolDefinition, ToolRegistry

__all__ = [
    "ToolAlreadyRegisteredError",
    "ToolNotFoundError",
    "InMemoryToolRegistry",
    "ToolRegistry",
    "ToolRegistrar",
]


class ToolAlreadyRegisteredError(ValueError):
    """Raised when trying to register a tool that already exists."""

    pass


class ToolNotFoundError(KeyError):
    """Raised when a tool is not found in the registry."""

    pass


class InMemoryToolRegistry(ToolRegistry):
    """
    In-memory implementation of ToolRegistry.

    Thread-safe for basic operations. Use for single-instance deployments.
    For multi-instance deployments, use a shared registry with Redis or similar.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._categories: dict[str, set[str]] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ToolAlreadyRegisteredError(f"Tool '{tool.name}' is already registered")

        self._tools[tool.name] = tool

        category = tool.category
        if category not in self._categories:
            self._categories[category] = set()
        self._categories[category].add(tool.name)

    def unregister(self, name: str) -> bool:
        tool = self._tools.pop(name, None)
        if tool is not None:
            category = tool.category
            if category in self._categories:
                self._categories[category].discard(name)
            return True
        return False

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def get_or_raise(self, name: str) -> Tool:
        """Get a tool or raise ToolNotFoundError."""
        tool = self.get(name)
        if tool is None:
            raise ToolNotFoundError(f"Tool '{name}' not found")
        return tool

    def list_all(self) -> list[Tool]:
        return list(self._tools.values())

    def list_by_category(self, category: str) -> list[Tool]:
        tool_names = self._categories.get(category, set())
        return [self._tools[name] for name in tool_names if name in self._tools]

    def get_schemas(self) -> list[ToolDefinition]:
        return [tool.to_definition() for tool in self._tools.values()]

    def search(self, query: str) -> list[Tool]:
        """
        Search tools by name, description, or tags.

        Case-insensitive matching. Supports partial matches.
        """
        query_lower = query.lower()
        results: list[tuple[int, Tool]] = []  # (score, tool)

        for tool in self._tools.values():
            score = 0

            # Exact name match gets highest score
            if tool.name.lower() == query_lower:
                score = 100
            # Prefix match
            elif tool.name.lower().startswith(query_lower):
                score = 80
            # Contains in name
            elif query_lower in tool.name.lower():
                score = 60
            # Contains in description
            elif query_lower in tool.description.lower():
                score = 40
            # Match in tags
            elif any(query_lower in tag.lower() for tag in tool.tags):
                score = 30

            if score > 0:
                results.append((score, tool))

        # Sort by score descending, then by name
        results.sort(key=lambda x: (-x[0], x[1].name))
        return [tool for _, tool in results]

    @property
    def tool_count(self) -> int:
        """Number of registered tools."""
        return len(self._tools)

    @property
    def categories(self) -> list[str]:
        """List of registered categories."""
        return list(self._categories.keys())

    def copy(self) -> Self:
        """Create a shallow copy of this registry."""
        new_registry = self.__class__()
        new_registry._tools = self._tools.copy()
        new_registry._categories = {cat: names.copy() for cat, names in self._categories.items()}
        return new_registry


class ToolRegistrar:
    """
    Helper class for registering multiple tools at once.

    Useful for batch registration and module setup.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._registered: list[str] = []

    def register(self, tool: Tool) -> Self:
        """Register a single tool and return self for chaining."""
        self._registry.register(tool)
        self._registered.append(tool.name)
        return self

    def register_many(self, tools: list[Tool]) -> Self:
        """Register multiple tools."""
        for tool in tools:
            self.register(tool)
        return self

    @property
    def registered(self) -> list[str]:
        """List of registered tool names."""
        return self._registered.copy()
