"""Tool Executor Service - wraps tool execution with circuit breaker and events."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from cortex.events import EventEmitter
from cortex.tools.interfaces import ToolCall, ToolExecutor, ToolResult

if TYPE_CHECKING:
    from cortex.events import EventBus


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"   # Normal operation
    OPEN = "open"       # Failing fast
    HALF_OPEN = "half_open"  # Testing recovery


class CircuitBreaker:
    """
    Circuit breaker pattern implementation.

    Prevents cascading failures by failing fast when a service is unhealthy.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        open_timeout_seconds: float = 30.0
    ):
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.open_timeout_seconds = open_timeout_seconds

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> CircuitState:
        return self._state

    @state.setter
    def state(self, value: CircuitState) -> None:
        self._state = value

    @property
    def failure_count(self) -> int:
        return self._failure_count

    @failure_count.setter
    def failure_count(self, value: int) -> None:
        self._failure_count = value

    def can_execute(self) -> bool:
        """Check if execution is allowed."""
        if self._state == CircuitState.CLOSED:
            return True

        if self._state == CircuitState.OPEN:
            # Check if timeout has passed
            if self._opened_at and (time.time() - self._opened_at) >= self.open_timeout_seconds:
                self._state = CircuitState.HALF_OPEN
                self._success_count = 0
                return True
            return False

        # HALF_OPEN always allows one attempt
        return True

    def record_failure(self) -> None:
        """Record a failure."""
        self._failure_count += 1

        if self._state == CircuitState.HALF_OPEN:
            # Failed in half-open, go back to open
            self._state = CircuitState.OPEN
            self._opened_at = time.time()
        elif self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.time()

    def record_success(self) -> None:
        """Record a success."""
        self._success_count += 1

        if self._state == CircuitState.HALF_OPEN:
            if self._success_count >= self.success_threshold:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._success_count = 0
        else:
            # Reset failure count in closed state
            self._failure_count = 0


@dataclass
class ServiceExecutionMetrics:
    """Metrics for tool executor service."""
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    rejected_calls: int = 0
    circuit_open_rejections: int = 0

    @property
    def success_rate(self) -> float:
        if self.total_calls == 0:
            return 0.0
        return self.successful_calls / self.total_calls


class ToolExecutorService:
    """
    Service layer for tool execution.

    Wraps base executor with:
    - Circuit breaker for fault tolerance
    - Event emission for monitoring
    - Metrics collection
    """

    def __init__(
        self,
        base_executor: ToolExecutor,
        event_bus: EventBus,
        circuit_breaker: CircuitBreaker | None = None
    ):
        self._base_executor = base_executor
        self._emitter = EventEmitter(event_bus, source_module="tool_executor_service")
        self._circuit_breaker = circuit_breaker or CircuitBreaker()
        self._metrics = ServiceExecutionMetrics()

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        return self._circuit_breaker

    @property
    def metrics(self) -> ServiceExecutionMetrics:
        return self._metrics

    async def execute(
        self,
        tool_call: ToolCall,
        *,
        timeout: int | None = None
    ) -> ToolResult:
        """
        Execute a tool call with circuit breaker and event emission.

        Args:
            tool_call: The tool call to execute
            timeout: Optional timeout override

        Returns:
            ToolResult with output or error
        """
        self._metrics.total_calls += 1

        # Check circuit breaker
        if not self._circuit_breaker.can_execute():
            self._metrics.rejected_calls += 1
            self._metrics.circuit_open_rejections += 1
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                success=False,
                error="Circuit breaker is open - service unavailable",
                error_severity=None,
                execution_time_ms=0
            )

        # Emit started event
        await self._emitter.emit("tool.started", {
            "tool_call_id": tool_call.id,
            "tool_name": tool_call.name,
            "arguments": tool_call.arguments,
            "timestamp": time.time()
        })

        try:
            # Execute via base executor
            result = await self._base_executor.execute(tool_call, timeout=timeout)

            # Update metrics based on result
            if result.success:
                self._circuit_breaker.record_success()
                self._metrics.successful_calls += 1
            else:
                self._circuit_breaker.record_failure()
                self._metrics.failed_calls += 1

            # Emit result event
            await self._emitter.emit("tool.result", {
                "tool_call_id": result.tool_call_id,
                "tool_name": result.tool_name,
                "success": result.success,
                "output": result.output,
                "error": result.error,
                "execution_time_ms": result.execution_time_ms,
                "timestamp": time.time()
            })

            return result

        except Exception as e:
            self._circuit_breaker.record_failure()
            self._metrics.failed_calls += 1

            error_result = ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                success=False,
                error=str(e)
            )

            await self._emitter.emit("tool.error", {
                "tool_call_id": tool_call.id,
                "tool_name": tool_call.name,
                "error": str(e),
                "timestamp": time.time()
            })

            return error_result

    async def execute_many(
        self,
        tool_calls: list[ToolCall],
        *,
        timeout: int | None = None,
        parallel: bool = False
    ) -> list[ToolResult]:
        """
        Execute multiple tool calls.

        Args:
            tool_calls: List of tool calls to execute
            timeout: Optional timeout per call
            parallel: If True, execute in parallel

        Returns:
            List of results in same order as calls
        """
        if parallel:
            tasks = [self.execute(call, timeout=timeout) for call in tool_calls]
            return await asyncio.gather(*tasks)
        else:
            results = []
            for call in tool_calls:
                result = await self.execute(call, timeout=timeout)
                results.append(result)
            return results

class ServiceToolExecutor(ToolExecutor):
    """
    ToolExecutor interface implementation that delegates to ToolExecutorService.

    Use this to wrap the service for dependency injection.
    """

    def __init__(self, service: ToolExecutorService):
        self._service = service

    async def execute(
        self,
        tool_call: ToolCall,
        *,
        timeout: int | None = None
    ) -> ToolResult:
        return await self._service.execute(tool_call, timeout=timeout)

    async def execute_many(
        self,
        tool_calls: list[ToolCall],
        *,
        timeout: int | None = None
    ) -> list[ToolResult]:
        return await self._service.execute_many(tool_calls, timeout=timeout)
