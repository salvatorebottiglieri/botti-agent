"""Per-task sandbox: confines tool execution to a temporary directory.

Each Eval Task runs inside its own sandbox directory (a ``mkdtemp`` tree
by default). The four meta tools are wrapped in :class:`SandboxedTool`
instances so the agent's file operations resolve inside the sandbox and
shell commands run with the sandbox as their working directory — the
agent works on the task, never on the host repo.

This is a convenience isolation boundary, not a security sandbox: a
scripted ``shell`` command can still reach host paths explicitly.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from cortex.eval.fixtures import SandboxFile
from cortex.tools.interfaces import Tool, ToolDefinition, ToolResult
from cortex.tools.meta import (
    AskUserTool,
    FileReadTool,
    FileWriteTool,
    GrepTool,
    ShellTool,
)


class SandboxEscapeError(ValueError):
    """Raised when a tool-supplied path would escape the sandbox."""


class TaskSandbox:
    """A temporary working directory a task's tools are confined to."""

    def __init__(self, root: str | Path | None = None) -> None:
        if root is None:
            root = Path(tempfile.mkdtemp(prefix="cortex-eval-"))
        self._root = Path(root).resolve()

    @property
    def root(self) -> Path:
        """Absolute path of the sandbox directory."""
        return self._root

    def setup(self, files: list[SandboxFile]) -> None:
        """Materialize the task's fixture files inside the sandbox."""
        for file in files:
            path = self.confine(file.path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(file.content, encoding="utf-8")

    def confine(self, path: str) -> Path:
        """Resolve a tool-supplied path inside the sandbox.

        Relative paths are joined to the sandbox root; absolute paths must
        already be inside it. Anything that would land outside the root
        raises :class:`SandboxEscapeError`.
        """
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self._root / candidate
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self._root):
            raise SandboxEscapeError(f"Path escapes the eval sandbox: {path}")
        return resolved

    def cleanup(self) -> None:
        """Remove the sandbox directory and its contents."""
        shutil.rmtree(self._root, ignore_errors=True)


class SandboxedTool(Tool):
    """Wraps a meta tool so its arguments stay inside the task sandbox.

    * ``file_read`` / ``file_write`` / ``grep`` paths are confined to the
      sandbox root (escapes fail the call).
    * ``shell`` commands run with the sandbox as their working directory.
    """

    _PATH_TOOLS = frozenset({"file_read", "file_write", "grep"})

    def __init__(self, tool: Tool, sandbox: TaskSandbox) -> None:
        self._tool = tool
        self._sandbox = sandbox

    @property
    def name(self) -> str:
        return self._tool.name

    @property
    def description(self) -> str:
        return self._tool.description

    @property
    def input_schema(self) -> dict[str, Any]:
        return self._tool.input_schema

    @property
    def output_schema(self) -> dict[str, Any] | None:
        return self._tool.output_schema

    @property
    def category(self) -> str:
        return self._tool.category

    @property
    def tags(self) -> list[str]:
        return self._tool.tags

    @property
    def idempotent(self) -> bool:
        return self._tool.idempotent

    @property
    def timeout_seconds(self) -> int:
        return self._tool.timeout_seconds

    def to_definition(self) -> ToolDefinition:
        return self._tool.to_definition()

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        confined = dict(arguments)
        if self.name in self._PATH_TOOLS and "path" in confined:
            confined["path"] = str(self._sandbox.confine(confined["path"]))
        elif self.name == "shell":
            confined["working_dir"] = str(self._sandbox.root)
        return await self._tool.execute(confined)


def build_sandboxed_tools(sandbox: TaskSandbox) -> list[Tool]:
    """The four file/shell meta tools, sandboxed, plus ask_user.

    ``ask_user`` needs no sandboxing (it touches no filesystem or shell), so it
    is added unwrapped — the agent can ask a clarifying question in any task.
    """
    return [
        SandboxedTool(tool, sandbox)
        for tool in (FileReadTool(), FileWriteTool(), GrepTool(), ShellTool())
    ] + [AskUserTool()]
