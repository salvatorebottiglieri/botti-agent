"""Tests for the per-task sandbox and sandboxed tool wrappers."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from cortex.eval.fixtures import SandboxFile
from cortex.eval.sandbox import SandboxedTool, SandboxEscapeError, TaskSandbox
from cortex.tools.executor import DefaultToolExecutor
from cortex.tools.interfaces import ToolCall
from cortex.tools.meta import FileReadTool, FileWriteTool, ShellTool
from cortex.tools.registry import InMemoryToolRegistry


class TestTaskSandbox:
    """The sandbox directory itself."""

    def test_root_created_under_tmp(self):
        """A sandbox without an explicit root lives under the system tmp."""
        sandbox = TaskSandbox()
        try:
            assert sandbox.root.exists()
            assert str(sandbox.root).startswith(tempfile.gettempdir())
        finally:
            sandbox.cleanup()
        assert not sandbox.root.exists()

    def test_setup_writes_files_with_parents(self, tmp_path):
        """setup() materializes fixture files, creating parent dirs."""
        sandbox = TaskSandbox(root=tmp_path / "s")
        sandbox.setup([SandboxFile(path="data/input.txt", content="40\n2")])
        assert (sandbox.root / "data" / "input.txt").read_text() == "40\n2"

    def test_confine_relative_path_inside_sandbox(self, tmp_path):
        """Relative paths resolve under the sandbox root."""
        sandbox = TaskSandbox(root=tmp_path)
        assert sandbox.confine("a/b.txt") == (tmp_path / "a" / "b.txt").resolve()

    def test_confine_rejects_parent_traversal(self, tmp_path):
        """'..' escapes are refused."""
        sandbox = TaskSandbox(root=tmp_path)
        with pytest.raises(SandboxEscapeError):
            sandbox.confine("../escape.txt")

    def test_confine_rejects_absolute_path_outside(self, tmp_path):
        """Absolute paths outside the sandbox are refused."""
        sandbox = TaskSandbox(root=tmp_path)
        with pytest.raises(SandboxEscapeError):
            sandbox.confine("/etc/passwd")


class TestSandboxedTool:
    """Tool wrappers confine execution to the sandbox."""

    async def test_file_write_lands_in_sandbox(self, tmp_path):
        """A relative write goes to the sandbox, not the host cwd."""
        sandbox = TaskSandbox(root=tmp_path)
        tool = SandboxedTool(FileWriteTool(), sandbox)
        result = await tool.execute({"path": "answer.txt", "content": "42"})
        assert result.success is True
        assert (sandbox.root / "answer.txt").read_text() == "42"
        assert not (os.getcwd() / Path("answer.txt")).exists()

    async def test_file_read_is_confined(self, tmp_path):
        """Reads resolve inside the sandbox."""
        sandbox = TaskSandbox(root=tmp_path)
        (sandbox.root / "note.txt").write_text("hello", encoding="utf-8")
        read_tool = SandboxedTool(FileReadTool(), sandbox)
        result = await read_tool.execute({"path": "note.txt"})
        assert result.success is True
        assert result.output == "hello"

    async def test_escape_attempt_fails_execution(self, tmp_path):
        """A traversal attempt surfaces as a failed tool result via the executor."""
        sandbox = TaskSandbox(root=tmp_path)
        registry = InMemoryToolRegistry()
        registry.register(SandboxedTool(FileWriteTool(), sandbox))
        executor = DefaultToolExecutor(registry=registry)
        result = await executor.execute(
            ToolCall(
                name="file_write",
                arguments={"path": "../evil.txt", "content": "x"},
            )
        )
        assert result.success is False
        assert not (tmp_path.parent / "evil.txt").exists()

    async def test_shell_runs_in_sandbox_working_dir(self, tmp_path):
        """shell commands run with the sandbox as their working directory."""
        sandbox = TaskSandbox(root=tmp_path)
        tool = SandboxedTool(ShellTool(), sandbox)
        result = await tool.execute({"command": "pwd"})
        assert result.success is True
        assert str(sandbox.root) in result.output

    async def test_shell_writes_stay_in_sandbox(self, tmp_path):
        """Files created by shell land in the sandbox, not the host cwd."""
        sandbox = TaskSandbox(root=tmp_path)
        tool = SandboxedTool(ShellTool(), sandbox)
        result = await tool.execute({"command": "echo hi > hello.txt"})
        assert result.success is True
        assert (sandbox.root / "hello.txt").read_text().strip() == "hi"
        assert not os.path.exists(os.path.join(os.getcwd(), "hello.txt"))
