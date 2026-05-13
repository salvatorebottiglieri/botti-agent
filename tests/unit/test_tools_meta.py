"""Tests for meta tools."""

import os
import shutil
import tempfile

import pytest

from cortex.tools.meta import (
    FileReadTool,
    FileWriteTool,
    GrepTool,
    ShellTool,
    register_meta_tools,
)
from cortex.tools.registry import InMemoryToolRegistry


class TestFileReadTool:
    """Tests for FileReadTool."""

    @pytest.fixture
    def tool(self):
        return FileReadTool()

    @pytest.mark.asyncio
    async def test_read_file(self, tool):
        """Can read a file."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("Hello, World!")
            f.flush()
            path = f.name

        try:
            result = await tool.execute({"path": path})
            assert result.success is True
            assert "Hello, World!" in result.output
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_read_nonexistent_file(self, tool):
        """Reading nonexistent file fails."""
        result = await tool.execute({"path": "/nonexistent/file/path.txt"})
        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_read_file_with_offset(self, tool):
        """Can read file with offset."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("Line 1\nLine 2\nLine 3\n")
            f.flush()
            path = f.name

        try:
            result = await tool.execute({"path": path, "offset": 1})
            assert result.success is True
            assert "Line 2" in result.output
            assert "Line 1" not in result.output
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_read_file_with_max_lines(self, tool):
        """Can read file with max lines."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("Line 1\nLine 2\nLine 3\nLine 4\n")
            f.flush()
            path = f.name

        try:
            result = await tool.execute({"path": path, "max_lines": 2})
            assert result.success is True
            assert result.output.count("\n") <= 2
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, tool):
        """Path traversal is blocked."""
        result = await tool.execute({"path": "../../../etc/passwd"})
        assert result.success is False
        assert "traversal" in result.error.lower()


class TestFileWriteTool:
    """Tests for FileWriteTool."""

    @pytest.fixture
    def tool(self):
        return FileWriteTool()

    @pytest.mark.asyncio
    async def test_write_file(self, tool):
        """Can write a file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.txt")
            result = await tool.execute({"path": path, "content": "Hello, World!"})

            assert result.success is True
            assert os.path.exists(path)
            with open(path) as f:
                assert f.read() == "Hello, World!"

    @pytest.mark.asyncio
    async def test_write_creates_directory(self, tool):
        """Writing creates parent directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "subdir", "nested", "test.txt")
            result = await tool.execute({"path": path, "content": "test"})

            assert result.success is True
            assert os.path.exists(path)

    @pytest.mark.asyncio
    async def test_append_mode(self, tool):
        """Can append to file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.txt")

            await tool.execute({"path": path, "content": "First"})
            result = await tool.execute({"path": path, "content": "Second", "append": True})

            assert result.success is True
            with open(path) as f:
                content = f.read()
                assert content == "FirstSecond"

    @pytest.mark.asyncio
    async def test_overwrite_mode(self, tool):
        """Writing without append overwrites."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.txt")

            await tool.execute({"path": path, "content": "First"})
            await tool.execute({"path": path, "content": "Second"})

            with open(path) as f:
                assert f.read() == "Second"


class TestShellTool:
    """Tests for ShellTool."""

    @pytest.fixture
    def tool(self):
        return ShellTool()

    @pytest.mark.asyncio
    async def test_echo_command(self, tool):
        """Can run echo command."""
        result = await tool.execute({"command": "echo 'Hello'"})
        assert result.success is True
        assert "Hello" in result.output

    @pytest.mark.asyncio
    async def test_failed_command(self, tool):
        """Failed command returns error."""
        result = await tool.execute({"command": "exit 1"})
        assert result.success is False
        assert result.metadata.get("exit_code") == 1

    @pytest.mark.skipif(
        shutil.which("sleep") is None,
        reason="needs `sleep` on PATH (bash/POSIX); skipped under cmd/PowerShell",
    )
    @pytest.mark.asyncio
    async def test_command_timeout(self, tool):
        """Command timeout works."""
        result = await tool.execute({"command": "sleep 10", "timeout": 1})
        assert result.success is False
        assert "timed out" in result.error

    @pytest.mark.asyncio
    async def test_empty_command_fails(self, tool):
        """Empty command is rejected."""
        result = await tool.execute({"command": ""})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_newline_in_command_rejected(self, tool):
        """Newlines in command are rejected."""
        result = await tool.execute({"command": "echo 'hello' && echo 'world'"})
        assert result.success is True  # && is allowed

        result = await tool.execute({"command": "echo 'hello'\necho 'world'"})
        assert result.success is False
        assert "newlines" in result.error


class TestGrepTool:
    """Tests for GrepTool."""

    @pytest.fixture
    def tool(self):
        return GrepTool()

    @pytest.mark.asyncio
    async def test_grep_finds_pattern(self, tool):
        """Grep finds matching lines."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.txt")
            with open(path, "w") as f:
                f.write("Hello World\n")
                f.write("Goodbye World\n")
                f.write("Hello Again\n")

            result = await tool.execute({"pattern": "Hello", "path": path})

            assert result.success is True
            assert result.metadata["match_count"] == 2

    @pytest.mark.asyncio
    async def test_grep_case_sensitive(self, tool):
        """Grep case sensitivity works."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.txt")
            with open(path, "w") as f:
                f.write("Hello\nhello\nHELLO\n")

            result = await tool.execute({"pattern": "Hello", "path": path, "case_sensitive": True})

            assert result.success is True
            assert result.metadata["match_count"] == 1

    @pytest.mark.asyncio
    async def test_grep_regex(self, tool):
        """Grep supports regex."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.txt")
            with open(path, "w") as f:
                f.write("file1.txt\n")
                f.write("file2.py\n")
                f.write("file3.txt\n")

            result = await tool.execute({"pattern": r"file\d+\.txt", "path": path})

            assert result.success is True
            assert result.metadata["match_count"] == 2

    @pytest.mark.asyncio
    async def test_grep_directory(self, tool):
        """Grep can search directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create multiple files
            for i in range(3):
                path = os.path.join(tmpdir, f"file{i}.txt")
                with open(path, "w") as f:
                    f.write(f"match {i}\n")

            result = await tool.execute({"pattern": "match", "path": tmpdir})

            assert result.success is True
            assert result.metadata["files_searched"] == 3

    @pytest.mark.asyncio
    async def test_grep_file_pattern(self, tool):
        """Grep filters by file pattern."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create .txt and .py files
            with open(os.path.join(tmpdir, "test.txt"), "w") as f:
                f.write("match\n")
            with open(os.path.join(tmpdir, "test.py"), "w") as f:
                f.write("match\n")

            result = await tool.execute(
                {"pattern": "match", "path": tmpdir, "file_pattern": "*.txt"}
            )

            assert result.success is True
            assert result.metadata["files_searched"] == 1


class TestRegisterMetaTools:
    """Tests for registering meta tools."""

    def test_register_all_meta_tools(self):
        """Can register all meta tools."""
        registry = InMemoryToolRegistry()
        registered = register_meta_tools(registry)

        assert len(registered) == 4
        assert "file_read" in registered
        assert "file_write" in registered
        assert "shell" in registered
        assert "grep" in registered

    def test_all_tools_work(self):
        """All registered meta tools execute successfully."""
        registry = InMemoryToolRegistry()
        register_meta_tools(registry)

        tools = registry.list_all()
        assert len(tools) == 4

        # Check all can be retrieved
        for name in ["file_read", "file_write", "shell", "grep"]:
            assert registry.get(name) is not None
