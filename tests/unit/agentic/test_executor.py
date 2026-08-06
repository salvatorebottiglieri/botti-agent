"""Tests for LoopExecutor."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from cortex.agentic.executor import LoopExecutor
from cortex.tools.interfaces import ToolCall, ToolResult


class TestLoopExecutor:
    """Tests for LoopExecutor."""

    @pytest.fixture
    def mock_tool_executor(self):
        """Create a mock tool executor."""
        executor = MagicMock()
        executor.execute = AsyncMock()
        executor.execute_many = AsyncMock()
        return executor

    @pytest.fixture
    def mock_event_bus(self):
        """Create a mock event bus."""
        bus = MagicMock()
        bus.publish = AsyncMock()
        return bus

    @pytest.fixture
    def executor(self, mock_tool_executor, mock_event_bus):
        """Create a LoopExecutor."""
        return LoopExecutor(
            tool_executor=mock_tool_executor,
            event_bus=mock_event_bus,
        )

    @pytest.mark.asyncio
    async def test_execute_tools_returns_results(self, executor, mock_tool_executor):
        """Execute tools should return results."""
        tool_calls = [
            ToolCall(id="1", name="file_read", arguments={"path": "/test"}),
        ]

        mock_tool_executor.execute = AsyncMock(return_value=ToolResult(
            tool_call_id="1",
            tool_name="file_read",
            success=True,
            output="file content",
        ))

        results = await executor.execute_tools(tool_calls)

        assert len(results) == 1
        assert results[0].success

    @pytest.mark.asyncio
    async def test_execute_tools_parallel(self, mock_tool_executor, mock_event_bus):
        """Execute tools can run in parallel via semaphore."""
        tool_calls = [
            ToolCall(id="1", name="grep", arguments={"pattern": "TODO"}),
            ToolCall(id="2", name="grep", arguments={"pattern": "FIXME"}),
        ]

        # Mock execute to return results
        mock_tool_executor.execute = AsyncMock(side_effect=[
            ToolResult(tool_call_id="1", tool_name="grep", success=True, output="TODO1"),
            ToolResult(tool_call_id="2", tool_name="grep", success=True, output="FIXME1"),
        ])

        executor = LoopExecutor(
            tool_executor=mock_tool_executor,
            event_bus=mock_event_bus,
        )

        results = await executor.execute_tools(tool_calls, parallel=True)

        # Both tools should have been executed
        assert mock_tool_executor.execute.call_count == 2
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_execute_single(self, executor, mock_tool_executor):
        """Execute single tool."""
        tool_call = ToolCall(id="1", name="shell", arguments={"command": "ls"})

        mock_tool_executor.execute = AsyncMock(return_value=ToolResult(
            tool_call_id="1",
            tool_name="shell",
            success=True,
            output="files",
        ))

        result = await executor.execute_single(tool_call)

        assert result.success

    @pytest.mark.asyncio
    async def test_execute_emits_start_event(self, executor, mock_event_bus):
        """Execute should emit events."""
        mock_tool_executor = MagicMock()
        mock_tool_executor.execute = AsyncMock(return_value=ToolResult(
            tool_call_id="1",
            tool_name="file_read",
            success=True,
            output="ok",
        ))

        exec_instance = LoopExecutor(
            tool_executor=mock_tool_executor,
            event_bus=mock_event_bus,
        )

        tool_call = ToolCall(id="1", name="file_read", arguments={})

        await exec_instance.execute_tools([tool_call])

        # Event bus should have been called
        assert mock_event_bus.publish.called or mock_event_bus.emit.called

    @pytest.mark.asyncio
    async def test_execute_handles_failure(self, executor, mock_tool_executor):
        """Execute handles tool failures."""
        tool_call = ToolCall(id="1", name="file_read", arguments={"path": "/nonexistent"})

        mock_tool_executor.execute = AsyncMock(return_value=ToolResult(
            tool_call_id="1",
            tool_name="file_read",
            success=False,
            error="File not found",
        ))

        result = await executor.execute_single(tool_call)

        assert not result.success
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_execute_many_sequential(self, executor, mock_tool_executor):
        """Execute many can run sequentially."""
        tool_calls = [
            ToolCall(id="1", name="file_read", arguments={"path": "/a"}),
            ToolCall(id="2", name="file_read", arguments={"path": "/b"}),
        ]

        mock_tool_executor.execute = AsyncMock(return_value=ToolResult(
            tool_call_id="1",
            tool_name="file_read",
            success=True,
            output="content",
        ))

        results = await executor.execute_tools(tool_calls, parallel=False)

        assert len(results) == 2


class TestLoopExecutorEdgeCases:
    """Edge case tests for LoopExecutor."""

    @pytest.fixture
    def mock_tool_executor(self):
        return MagicMock(spec=['execute', 'execute_many'])

    @pytest.fixture
    def mock_event_bus(self):
        bus = MagicMock()
        bus.publish = AsyncMock()
        return bus

    @pytest.mark.asyncio
    async def test_execute_empty_tool_calls(self, mock_tool_executor, mock_event_bus):
        """Execute handles empty tool calls."""
        executor = LoopExecutor(
            tool_executor=mock_tool_executor,
            event_bus=mock_event_bus,
        )

        results = await executor.execute_tools([])

        assert results == []

    @pytest.mark.asyncio
    async def test_execute_with_timeout(self, mock_tool_executor, mock_event_bus):
        """Execute passes timeout to tool executor."""
        mock_tool_executor.execute = AsyncMock(return_value=ToolResult(
            tool_call_id="1",
            tool_name="shell",
            success=True,
            output="done",
        ))

        executor = LoopExecutor(
            tool_executor=mock_tool_executor,
            event_bus=mock_event_bus,
        )

        tool_call = ToolCall(id="1", name="shell", arguments={"cmd": "sleep 1"})

        await executor.execute_tools([tool_call], timeout=30)

        # Verify timeout was passed
        call_kwargs = mock_tool_executor.execute.call_args.kwargs
        assert 'timeout' in call_kwargs

    @pytest.mark.asyncio
    async def test_execute_allows_partial_failures(self, mock_tool_executor, mock_event_bus):
        """Execute allows some tools to fail while others succeed."""
        mock_tool_executor.execute = AsyncMock(return_value=ToolResult(
            tool_call_id="1",
            tool_name="file_read",
            success=False,
            error="Not found",
        ))

        executor = LoopExecutor(
            tool_executor=mock_tool_executor,
            event_bus=mock_event_bus,
        )

        tool_calls = [
            ToolCall(id="1", name="file_read", arguments={"path": "/missing"}),
            ToolCall(id="2", name="file_read", arguments={"path": "/exists"}),
        ]

        # First call fails
        mock_tool_executor.execute.side_effect = [
            ToolResult(tool_call_id="1", tool_name="file_read", success=False, error="Not found"),
            ToolResult(tool_call_id="2", tool_name="file_read", success=True, output="content"),
        ]

        results = await executor.execute_tools(tool_calls, parallel=False)

        # Both results should be returned
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_execute_handles_exception(self, mock_tool_executor, mock_event_bus):
        """Execute handles exceptions from tool executor."""
        mock_tool_executor.execute.side_effect = Exception("Tool crashed")

        executor = LoopExecutor(
            tool_executor=mock_tool_executor,
            event_bus=mock_event_bus,
        )

        tool_call = ToolCall(id="1", name="shell", arguments={"cmd": "crash"})

        result = await executor.execute_single(tool_call)

        # Should return error result, not raise
        assert not result.success
        assert "crashed" in result.error.lower() or "exception" in result.error.lower()
