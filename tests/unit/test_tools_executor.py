"""Tests for tool executor."""

import pytest
import asyncio

from cortex.tools.interfaces import Tool, ToolCall, ToolResult, ToolErrorSeverity
from cortex.tools.executor import (
    DefaultToolExecutor,
    ExecutionMetrics,
    ToolNotFound,
    ToolTimeout,
    ToolValidationError,
)
from cortex.tools.registry import InMemoryToolRegistry


class MockTool(Tool):
    """Mock tool for testing."""
    
    def __init__(
        self,
        name: str = "mock_tool",
        should_succeed: bool = True,
        execution_time: float = 0.01,
        error_message: str = "Mock error"
    ):
        self._name = name
        self._should_succeed = should_succeed
        self._execution_time = execution_time
        self._error_message = error_message
    
    @property
    def name(self) -> str:
        return self._name
    
    @property
    def description(self) -> str:
        return f"A mock tool named {self._name}"
    
    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "value": {"type": "string"}
            },
            "required": ["value"]
        }
    
    async def execute(self, arguments):
        await asyncio.sleep(self._execution_time)
        
        if not self._should_succeed:
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                success=False,
                error=self._error_message
            )
        
        return ToolResult(
            tool_call_id="",
            tool_name=self.name,
            success=True,
            output=f"Processed: {arguments.get('value', '')}"
        )


class SlowTool(Tool):
    """Tool that takes a long time to execute."""
    
    def __init__(self, delay: float = 2.0):
        self._delay = delay
    
    @property
    def name(self) -> str:
        return "slow_tool"
    
    @property
    def description(self) -> str:
        return "A slow tool"
    
    @property
    def timeout_seconds(self) -> int:
        return 1  # 1 second timeout
    
    async def execute(self, arguments):
        await asyncio.sleep(self._delay)
        return ToolResult(
            tool_call_id="",
            tool_name=self.name,
            success=True,
            output="done"
        )


class TestExecutionMetrics:
    """Tests for ExecutionMetrics."""
    
    def test_empty_metrics(self):
        """Empty metrics have zero values."""
        metrics = ExecutionMetrics()
        assert metrics.total_calls == 0
        assert metrics.success_rate == 0.0
        assert metrics.average_execution_ms == 0.0
    
    def test_success_rate(self):
        """Success rate calculated correctly."""
        metrics = ExecutionMetrics()
        metrics.total_calls = 10
        metrics.successful_calls = 7
        assert metrics.success_rate == 0.7


class TestDefaultToolExecutor:
    """Tests for DefaultToolExecutor."""
    
    @pytest.fixture
    def registry(self):
        """Create a registry with some tools."""
        registry = InMemoryToolRegistry()
        registry.register(MockTool("tool1"))
        registry.register(MockTool("tool2"))
        return registry
    
    @pytest.fixture
    def executor(self, registry):
        """Create an executor with the registry."""
        return DefaultToolExecutor(registry)
    
    @pytest.mark.asyncio
    async def test_execute_tool_success(self, executor):
        """Successful tool execution returns result."""
        tool_call = ToolCall(name="tool1", arguments={"value": "test"})
        result = await executor.execute(tool_call)
        
        assert result.success is True
        assert result.tool_name == "tool1"
        assert "test" in result.output
    
    @pytest.mark.asyncio
    async def test_execute_tool_not_found(self, executor):
        """Executing nonexistent tool returns error."""
        tool_call = ToolCall(name="nonexistent", arguments={})
        result = await executor.execute(tool_call)
        
        assert result.success is False
        assert "not found" in result.error
    
    @pytest.mark.asyncio
    async def test_execute_tool_failure(self, executor):
        """Tool failure returns error result."""
        # Add a failing tool
        executor._registry.register(MockTool("failing", should_succeed=False))
        
        tool_call = ToolCall(name="failing", arguments={"value": "test"})
        result = await executor.execute(tool_call)
        
        assert result.success is False
        assert result.error == "Mock error"
    
    @pytest.mark.asyncio
    async def test_execute_with_timeout(self, executor):
        """Tool timeout returns error."""
        # Add slow tool
        executor._registry.register(SlowTool(delay=2.0))
        
        tool_call = ToolCall(name="slow_tool", arguments={})
        result = await executor.execute(tool_call, timeout=0.5)
        
        assert result.success is False
        assert "timed out" in result.error
        assert result.error_severity == ToolErrorSeverity.ERROR
    
    @pytest.mark.asyncio
    async def test_execute_tool_timeout_default(self, executor):
        """Tool uses its own timeout if not specified."""
        # Add slow tool
        executor._registry.register(SlowTool(delay=2.0))
        
        tool_call = ToolCall(name="slow_tool", arguments={})
        result = await executor.execute(tool_call)  # No timeout override
        
        # Should timeout using tool's default (1 second)
        assert result.success is False
        assert "timed out" in result.error
    
    @pytest.mark.asyncio
    async def test_validation_failure(self):
        """Invalid arguments fail validation."""
        executor = DefaultToolExecutor(strict_validation=True)
        executor._registry.register(MockTool("tool1"))
        
        tool_call = ToolCall(name="tool1", arguments={})  # Missing required "value"
        result = await executor.execute(tool_call)
        
        assert result.success is False
        assert "validation" in result.error.lower()
    
    @pytest.mark.asyncio
    async def test_validation_disabled(self):
        """Validation can be disabled."""
        executor = DefaultToolExecutor(strict_validation=False)
        executor._registry.register(MockTool("tool1", should_succeed=False))
        
        tool_call = ToolCall(name="tool1", arguments={})  # Missing required "value"
        result = await executor.execute(tool_call)
        
        # Should still execute despite missing required arg
        assert result.success is False  # Because tool itself fails
        assert "validation" not in result.error.lower()
    
    @pytest.mark.asyncio
    async def test_metrics_updated(self, executor):
        """Metrics are updated after execution."""
        tool_call = ToolCall(name="tool1", arguments={"value": "test"})
        await executor.execute(tool_call)
        
        metrics = executor.metrics
        assert metrics.total_calls == 1
        assert metrics.successful_calls == 1
        assert metrics.failed_calls == 0
    
    @pytest.mark.asyncio
    async def test_execute_many_sequential(self, executor):
        """Execute many runs sequentially by default."""
        calls = [
            ToolCall(name="tool1", arguments={"value": "1"}),
            ToolCall(name="tool2", arguments={"value": "2"}),
        ]
        
        results = await executor.execute_many(calls)
        
        assert len(results) == 2
        assert all(r.success for r in results)
    
    @pytest.mark.asyncio
    async def test_execute_many_parallel(self, executor):
        """Execute many can run in parallel."""
        calls = [
            ToolCall(name="tool1", arguments={"value": "1"}),
            ToolCall(name="tool2", arguments={"value": "2"}),
        ]
        
        results = await executor.execute_many(calls, parallel=True)
        
        assert len(results) == 2
        assert all(r.success for r in results)
    
    @pytest.mark.asyncio
    async def test_execution_time_recorded(self, executor):
        """Execution time is recorded."""
        tool_call = ToolCall(name="tool1", arguments={"value": "test"})
        result = await executor.execute(tool_call)
        
        assert result.execution_time_ms is not None
        assert result.execution_time_ms > 0
    
    @pytest.mark.asyncio
    async def test_tool_call_id_preserved(self, executor):
        """Tool call ID is preserved in result."""
        custom_id = "call-12345"
        tool_call = ToolCall(id=custom_id, name="tool1", arguments={"value": "test"})
        result = await executor.execute(tool_call)
        
        assert result.tool_call_id == custom_id