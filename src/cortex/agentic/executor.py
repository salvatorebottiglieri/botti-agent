"""LoopExecutor - Tool execution within the agent loop."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from cortex.tools.interfaces import ToolCall, ToolResult

if TYPE_CHECKING:
    from cortex.tools.interfaces import ToolExecutor
    from cortex.events import EventBus

logger = logging.getLogger(__name__)


class LoopExecutor:
    """
    Tool execution within the agent loop.

    Handles:
    - Single and batch tool execution
    - Sequential and parallel execution
    - Event emission for monitoring
    - Error handling and recovery
    """

    def __init__(
        self,
        tool_executor: ToolExecutor,
        event_bus: EventBus | None = None,
        max_parallel: int = 5,
    ):
        self._executor = tool_executor
        self._event_bus = event_bus
        self._max_parallel = max_parallel

    async def execute_tools(
        self,
        tool_calls: list[ToolCall],
        *,
        timeout: int | None = None,
        parallel: bool = False,
    ) -> list[ToolResult]:
        """
        Execute tool calls.

        Args:
            tool_calls: List of tool calls to execute
            timeout: Optional timeout per call
            parallel: If True, execute in parallel (independent tools only)

        Returns:
            List of results in same order as calls
        """
        if not tool_calls:
            return []

        if parallel:
            return await self._execute_parallel(tool_calls, timeout)
        else:
            return await self._execute_sequential(tool_calls, timeout)

    async def execute_single(
        self,
        tool_call: ToolCall,
        *,
        timeout: int | None = None,
    ) -> ToolResult:
        """
        Execute a single tool call.

        Args:
            tool_call: Tool call to execute
            timeout: Optional timeout

        Returns:
            ToolResult
        """
        return await self._execute_with_tracking(tool_call, timeout)

    async def _execute_sequential(
        self,
        tool_calls: list[ToolCall],
        timeout: int | None = None,
    ) -> list[ToolResult]:
        """Execute tools sequentially."""
        results = []
        for call in tool_calls:
            result = await self._execute_with_tracking(call, timeout)
            results.append(result)
        return results

    async def _execute_parallel(
        self,
        tool_calls: list[ToolCall],
        timeout: int | None = None,
    ) -> list[ToolResult]:
        """Execute tools in parallel."""
        import asyncio

        # Limit concurrency
        semaphore = asyncio.Semaphore(self._max_parallel)

        async def execute_with_semaphore(call: ToolCall) -> ToolResult:
            async with semaphore:
                return await self._execute_with_tracking(call, timeout)

        tasks = [execute_with_semaphore(call) for call in tool_calls]
        return await asyncio.gather(*tasks)

    async def _execute_with_tracking(
        self,
        tool_call: ToolCall,
        timeout: int | None = None,
    ) -> ToolResult:
        """Execute a tool with event tracking."""
        start_time = time.time()

        # Emit start event
        await self._emit_event("agent.tool.started", {
            "tool_call_id": tool_call.id,
            "tool_name": tool_call.name,
            "arguments": tool_call.arguments,
            "timestamp": start_time,
        })

        try:
            # Execute
            result = await self._executor.execute(tool_call, timeout=timeout)

            # Emit completion event
            await self._emit_event("agent.tool.completed", {
                "tool_call_id": tool_call.id,
                "tool_name": tool_call.name,
                "success": result.success,
                "execution_time_ms": (time.time() - start_time) * 1000,
                "timestamp": time.time(),
            })

            return result

        except Exception as e:
            # Emit error event
            await self._emit_event("agent.tool.error", {
                "tool_call_id": tool_call.id,
                "tool_name": tool_call.name,
                "error": str(e),
                "timestamp": time.time(),
            })

            # Return error result
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                success=False,
                error=str(e),
                execution_time_ms=(time.time() - start_time) * 1000,
            )

    async def _emit_event(self, event_type: str, data: dict) -> None:
        """Emit an event to the event bus."""
        if not self._event_bus:
            return

        try:
            from cortex.events import BaseEvent
            event = BaseEvent.create(
                event_type=event_type,
                payload=data,
                source_module="loop_executor"
            )
            if hasattr(self._event_bus, 'publish'):
                await self._event_bus.publish(event)
        except Exception as e:
            # Don't let event failures affect tool execution
            logger.warning(f"Failed to emit event {event_type}: {e}")