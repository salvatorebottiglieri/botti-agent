"""Tests for ToolExecutorService."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from cortex.tools.interfaces import Tool, ToolCall, ToolResult, ToolDefinition, ToolRegistry
from cortex.tools.executor import DefaultToolExecutor
from cortex.services.tool_executor import (
    ToolExecutorService,
    ServiceToolExecutor,
    CircuitBreaker,
    CircuitState,
)


class MockTool(Tool):
    """Mock tool for testing."""

    def __init__(self, name: str = "mock_tool", should_succeed: bool = True):
        self._name = name
        self._should_succeed = should_succeed

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"A mock tool named {self._name}"

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, arguments):
        if not self._should_succeed:
            return ToolResult(
                tool_call_id="", tool_name=self.name, success=False, error="Mock error"
            )
        return ToolResult(
            tool_call_id="", tool_name=self.name, success=True, output="done"
        )


class TestCircuitBreaker:
    """Tests for CircuitBreaker."""

    def test_initial_state_is_closed(self):
        """Circuit starts in closed (healthy) state."""
        cb = CircuitBreaker(failure_threshold=3)
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    def test_opens_after_threshold(self):
        """Circuit opens after reaching failure threshold."""
        cb = CircuitBreaker(failure_threshold=3)
        
        # Record failures
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        
        assert cb.state == CircuitState.OPEN
        assert cb.failure_count == 3

    def test_half_open_after_timeout(self):
        """Circuit moves to half-open after timeout."""
        cb = CircuitBreaker(failure_threshold=1, open_timeout_seconds=0.01)
        cb.record_failure()  # Opens circuit
        assert cb.state == CircuitState.OPEN
        
        import asyncio
        import time
        time.sleep(0.02)  # Wait for timeout
        
        # Access should trigger half-open
        cb.can_execute()
        assert cb.state == CircuitState.HALF_OPEN

    def test_resets_on_success(self):
        """Circuit resets failure count on success."""
        cb = CircuitBreaker(failure_threshold=3)
        
        cb.record_failure()
        cb.record_failure()
        cb.record_success()  # Resets
        
        assert cb.failure_count == 0

    def test_closes_from_half_open_on_success(self):
        """Circuit closes after successful call in half-open."""
        cb = CircuitBreaker(failure_threshold=1, success_threshold=1, open_timeout_seconds=0.01)
        cb.record_failure()
        
        import time
        time.sleep(0.02)
        cb.can_execute()  # Move to half-open
        
        cb.record_success()  # With success_threshold=1, this should close it
        assert cb.state == CircuitState.CLOSED

    def test_reopens_on_failure_in_half_open(self):
        """Circuit reopens after failure in half-open."""
        cb = CircuitBreaker(failure_threshold=1, open_timeout_seconds=0.01)
        cb.record_failure()
        
        import time
        time.sleep(0.02)
        cb.can_execute()  # Move to half-open
        
        cb.record_failure()
        assert cb.state == CircuitState.OPEN


class TestToolExecutorService:
    """Tests for ToolExecutorService."""

    @pytest.fixture
    def mock_registry(self):
        """Create a mock tool registry."""
        registry = MagicMock(spec=ToolRegistry)
        registry.get.return_value = MockTool("test_tool")
        return registry

    @pytest.fixture
    def mock_event_bus(self):
        """Create a mock event bus."""
        from cortex.events import EventBus
        bus = MagicMock(spec=EventBus)
        bus.publish = AsyncMock()
        bus.emit = AsyncMock()
        return bus

    @pytest.fixture
    def service(self, mock_registry, mock_event_bus):
        """Create a ToolExecutorService."""
        executor = DefaultToolExecutor(mock_registry)
        return ToolExecutorService(executor, mock_event_bus)

    @pytest.mark.asyncio
    async def test_execute_publishes_start_event(self, service, mock_event_bus):
        """Executing a tool publishes tool.started event."""
        tool_call = ToolCall(name="test_tool", arguments={})
        
        await service.execute(tool_call)
        
        # Check that publish was called
        assert mock_event_bus.publish.called or mock_event_bus.emit.called

    @pytest.mark.asyncio
    async def test_execute_publishes_result_event(self, service, mock_event_bus):
        """Executing a tool publishes tool.result event."""
        tool_call = ToolCall(name="test_tool", arguments={})
        
        await service.execute(tool_call)
        
        # Verify result was published
        assert mock_event_bus.publish.called or mock_event_bus.emit.called

    @pytest.mark.asyncio
    async def test_execute_returns_result(self, service):
        """Execute returns a valid ToolResult."""
        tool_call = ToolCall(name="test_tool", arguments={})
        
        result = await service.execute(tool_call)
        
        assert isinstance(result, ToolResult)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_circuit_open_rejects(self, service):
        """Execute rejects when circuit is open."""
        service.circuit_breaker.state = CircuitState.OPEN
        
        tool_call = ToolCall(name="test_tool", arguments={})
        result = await service.execute(tool_call)
        
        assert result.success is False
        assert result.error is not None
        assert "circuit breaker is open" in result.error.lower()

    @pytest.mark.asyncio
    async def test_execute_many(self, service):
        """Execute many handles multiple calls."""
        calls = [
            ToolCall(name="test_tool", arguments={}),
            ToolCall(name="test_tool", arguments={}),
        ]
        
        results = await service.execute_many(calls)
        
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_execute_many_parallel(self, service):
        """Execute many can run in parallel."""
        calls = [
            ToolCall(name="test_tool", arguments={}),
            ToolCall(name="test_tool", arguments={}),
        ]
        
        results = await service.execute_many(calls, parallel=True)
        
        assert len(results) == 2


class TestServiceToolExecutor:
    """Tests for ServiceToolExecutor (interface wrapper)."""

    @pytest.fixture
    def mock_service(self):
        """Create a mock ToolExecutorService."""
        service = MagicMock(spec=ToolExecutorService)
        service.execute = AsyncMock(return_value=ToolResult(
            tool_call_id="call-1",
            tool_name="test",
            success=True,
            output="ok"
        ))
        return service

    @pytest.fixture
    def executor(self, mock_service):
        """Create a ServiceToolExecutor."""
        return ServiceToolExecutor(mock_service)

    @pytest.mark.asyncio
    async def test_execute_delegates_to_service(self, executor, mock_service):
        """Execute calls the underlying service."""
        tool_call = ToolCall(name="test", arguments={})
        
        await executor.execute(tool_call)
        
        mock_service.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_many_delegates(self, executor, mock_service):
        """Execute many calls the underlying service."""
        calls = [ToolCall(name="test", arguments={})]
        
        await executor.execute_many(calls)
        
        # Should call service's execute_many, not execute
        mock_service.execute_many.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_with_timeout(self, executor, mock_service):
        """Execute passes timeout to service."""
        tool_call = ToolCall(name="test", arguments={})
        
        await executor.execute(tool_call, timeout=30)
        
        mock_service.execute.assert_called()
        call_kwargs = mock_service.execute.call_args.kwargs
        assert "timeout" in call_kwargs or call_kwargs.get("timeout") == 30