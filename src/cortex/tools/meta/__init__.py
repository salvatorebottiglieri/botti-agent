"""Meta tools - built-in tools for the agent."""

from cortex.tools.interfaces import Tool, ToolRegistry
from cortex.tools.meta.file_read import FileReadTool
from cortex.tools.meta.file_write import FileWriteTool
from cortex.tools.meta.grep import GrepTool
from cortex.tools.meta.shell import ShellTool

__all__ = [
    "FileReadTool",
    "FileWriteTool",
    "GrepTool",
    "ShellTool",
]


def register_meta_tools(registry: ToolRegistry) -> list[str]:
    """Register all meta tools with a registry."""
    from cortex.tools.registry import ToolRegistrar

    registrar = ToolRegistrar(registry)
    registrar.register_many([
        FileReadTool(),
        FileWriteTool(),
        GrepTool(),
        ShellTool(),
    ])

    return registrar.registered


def get_meta_tools() -> list[Tool]:
    """Get a list of all meta tool instances."""
    return [
        FileReadTool(),
        FileWriteTool(),
        GrepTool(),
        ShellTool(),
    ]
