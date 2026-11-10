"""Meta tools - built-in tools for the agent."""

import logging
from collections.abc import Sequence

from cortex.config.safety import SafetySettings
from cortex.safety.journal import SafetyStore
from cortex.tools.interfaces import Tool, ToolRegistry
from cortex.tools.meta.ask_user import AskUserTool
from cortex.tools.meta.file_read import FileReadTool
from cortex.tools.meta.file_write import FileWriteTool
from cortex.tools.meta.grep import GrepTool
from cortex.tools.meta.shell import ShellTool

logger = logging.getLogger(__name__)

__all__ = [
    "AskUserTool",
    "FileReadTool",
    "FileWriteTool",
    "GrepTool",
    "ShellTool",
]


def register_meta_tools(
    registry: ToolRegistry,
    allowed: Sequence[str] | None = None,
    safety: SafetySettings | None = None,
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

    meta_tools = get_meta_tools(safety)
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

    # Journal the bypass only when the guarded tool is actually registered:
    # a disabled guard on a deployment without ``shell`` is not an event, and
    # an unconditional marker would sit in front of ``undo last``.
    if safety is not None and not safety.shell_guard and "shell" in registrar.registered:
        try:
            SafetyStore(safety).record_guard_disabled()
        except Exception as exc:  # never abort startup over the journal
            logger.warning(
                "could not journal that SAFETY_SHELL_GUARD is disabled: %s", exc
            )

    return registrar.registered


def get_meta_tools(safety: SafetySettings | None = None) -> list[Tool]:
    """Get a list of all meta tool instances.

    ``safety`` enables pre-overwrite snapshots in ``file_write`` and the
    deny-list in ``shell``; without it (the eval sandbox and existing tests)
    both tools behave as before.
    """
    return [
        FileReadTool(),
        FileWriteTool(safety),
        GrepTool(),
        ShellTool(safety),
        AskUserTool(),
    ]
