"""Meta tools - built-in tools for the agent."""

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


def register_meta_tools(registry):
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


def get_meta_tools() -> list:
    """Get a list of all meta tool instances."""
    return [
        FileReadTool(),
        FileWriteTool(),
        GrepTool(),
        ShellTool(),
    ]