"""Meta tools - built-in tools for the agent."""

from collections.abc import Sequence

from cortex.tools.interfaces import Tool, ToolRegistry
from cortex.tools.meta.ask_user import AskUserTool
from cortex.tools.meta.file_read import FileReadTool
from cortex.tools.meta.file_write import FileWriteTool
from cortex.tools.meta.grep import GrepTool
from cortex.tools.meta.shell import ShellTool

__all__ = [
    "AskUserTool",
    "FileReadTool",
    "FileWriteTool",
    "GrepTool",
    "ShellTool",
]


def register_meta_tools(
    registry: ToolRegistry, allowed: Sequence[str] | None = None
) -> list[str]:
    """Register meta tools with a registry, limited to ``allowed`` names.

    ``allowed=None`` registers every meta tool (the pre-#134 behaviour). An
    explicit sequence is a deny-by-default allow-list: the whole list is
    validated **before** anything is registered, so an unknown name — a typo in
    ``TOOLS_ALLOW`` — raises ``ValueError`` and leaves the registry untouched,
    failing startup loudly instead of silently dropping a capability.

    Returns the registered tool names, in the canonical order of
    :func:`get_meta_tools`.
    """
    from cortex.tools.registry import ToolRegistrar

    meta_tools = get_meta_tools()
    known_names = {tool.name for tool in meta_tools}
    allowed_names = known_names if allowed is None else set(allowed)

    unknown = allowed_names - known_names
    if unknown:
        known = ", ".join(tool.name for tool in meta_tools)
        raise ValueError(
            f"unknown meta tool(s): {', '.join(sorted(unknown))}; known: {known}"
        )

    selected = [tool for tool in meta_tools if tool.name in allowed_names]
    registrar = ToolRegistrar(registry)
    registrar.register_many(selected)

    return registrar.registered


def get_meta_tools() -> list[Tool]:
    """Get a list of all meta tool instances."""
    return [
        FileReadTool(),
        FileWriteTool(),
        GrepTool(),
        ShellTool(),
        AskUserTool(),
    ]
