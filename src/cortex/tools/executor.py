"""Tool execution with error handling and timeouts."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from cortex.tools.interfaces import (
    Tool,
    ToolCall,
    ToolErrorSeverity,
    ToolExecutor,
    ToolResult,
)
from cortex.tools.registry import InMemoryToolRegistry, ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class ExecutionMetrics:
    """Metrics for tool execution."""

    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    total_execution_ms: float = 0.0

    @property
    def success_rate(self) -> float:
        if self.total_calls == 0:
            return 0.0
        return self.successful_calls / self.total_calls

    @property
    def average_execution_ms(self) -> float:
        if self.total_calls == 0:
            return 0.0
        return self.total_execution_ms / self.total_calls


class ToolExecutionError(Exception):
    """Base exception for tool execution errors."""

    pass


class ToolNotFound(ToolExecutionError):  # noqa: N818
    """Raised when a tool is not found."""

    pass


class ToolTimeout(ToolExecutionError):  # noqa: N818
    """Raised when tool execution times out."""

    pass


class ToolValidationError(ToolExecutionError):
    """Raised when tool arguments fail validation."""

    pass


class DefaultToolExecutor(ToolExecutor):
    """
    Default implementation of ToolExecutor.

    Features:
    - Timeout handling
    - Error isolation (one tool failure doesn't crash executor)
    - Metrics collection
    - Validation of arguments against schema
    """

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        default_timeout: int = 60,
        strict_validation: bool = True,
    ) -> None:
        self._registry = registry or InMemoryToolRegistry()
        self._default_timeout = default_timeout
        self._strict_validation = strict_validation
        self._metrics = ExecutionMetrics()

    @property
    def registry(self) -> ToolRegistry:
        """The tool registry used by this executor."""
        return self._registry

    @property
    def metrics(self) -> ExecutionMetrics:
        """Execution metrics."""
        return self._metrics

    async def execute(self, tool_call: ToolCall, *, timeout: int | None = None) -> ToolResult:
        """
        Execute a single tool call.

        Args:
            tool_call: ToolCall with name and arguments
            timeout: Optional timeout override in seconds

        Returns:
            ToolResult with output or error
        """
        self._metrics.total_calls += 1

        tool_name = tool_call.name
        tool = self._registry.get(tool_name)

        if tool is None:
            error_result = ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_name,
                success=False,
                error=f"Tool '{tool_name}' not found",
                error_severity=ToolErrorSeverity.ERROR,
            )
            self._metrics.failed_calls += 1
            return error_result

        timeout_seconds = timeout if timeout is not None else tool.timeout_seconds

        # Validate arguments
        if self._strict_validation:
            validation_error = self._validate_arguments(tool, tool_call.arguments)
            if validation_error:
                error_result = ToolResult(
                    tool_call_id=tool_call.id,
                    tool_name=tool_name,
                    success=False,
                    error=f"Argument validation failed: {validation_error}",
                    error_severity=ToolErrorSeverity.ERROR,
                )
                self._metrics.failed_calls += 1
                return error_result

        # Execute with timeout
        start_time = asyncio.get_event_loop().time()

        try:
            result = await asyncio.wait_for(
                tool.execute(tool_call.arguments), timeout=timeout_seconds
            )

            end_time = asyncio.get_event_loop().time()
            execution_time_ms = (end_time - start_time) * 1000

            result.tool_call_id = tool_call.id
            result.execution_time_ms = execution_time_ms
            result.tool_name = tool_name

            if result.success:
                self._metrics.successful_calls += 1
            else:
                self._metrics.failed_calls += 1

            self._metrics.total_execution_ms += execution_time_ms

            return result

        except asyncio.TimeoutError:  # noqa: UP041
            end_time = asyncio.get_event_loop().time()
            execution_time_ms = (end_time - start_time) * 1000

            error_result = ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_name,
                success=False,
                error=f"Tool execution timed out after {timeout_seconds} seconds",
                error_severity=ToolErrorSeverity.ERROR,
                execution_time_ms=execution_time_ms,
            )
            self._metrics.failed_calls += 1
            self._metrics.total_execution_ms += execution_time_ms

            logger.warning(f"Tool '{tool_name}' timed out after {timeout_seconds}s")

            return error_result

        except Exception as e:
            end_time = asyncio.get_event_loop().time()
            execution_time_ms = (end_time - start_time) * 1000

            error_result = ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_name,
                success=False,
                error=f"Tool execution failed: {str(e)}",
                error_severity=ToolErrorSeverity.ERROR,
                execution_time_ms=execution_time_ms,
            )
            self._metrics.failed_calls += 1
            self._metrics.total_execution_ms += execution_time_ms

            logger.exception(f"Tool '{tool_name}' raised exception")

            return error_result

    async def execute_many(
        self, tool_calls: list[ToolCall], *, timeout: int | None = None, parallel: bool = False
    ) -> list[ToolResult]:
        """
        Execute multiple tool calls.

        Args:
            tool_calls: List of tool calls
            timeout: Optional timeout per tool
            parallel: If True, execute in parallel; if False, sequential

        Returns:
            List of ToolResults in same order as calls
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

    def _validate_arguments(self, tool: Tool, arguments: dict[str, Any]) -> str | None:
        """
        Validate arguments against tool's input schema.

        Returns:
            None if valid, error message if invalid
        """
        schema = tool.input_schema
        required = schema.get("required", [])

        # Check required fields
        for field_name in required:
            if field_name not in arguments:
                return f"Missing required field: '{field_name}'"

        # Check types
        properties = schema.get("properties", {})
        for field_name, value in arguments.items():
            if field_name in properties:
                expected_type = properties[field_name].get("type")
                if expected_type and not self._check_type(value, expected_type):
                    return f"Field '{field_name}' has wrong type: expected {expected_type}"

        return None

    def _check_type(self, value: Any, expected_type: str) -> bool:
        """Check if value matches expected JSON Schema type."""
        type_map = {
            "string": str,
            "number": (int, float),
            "integer": int,
            "boolean": bool,
            "array": list,
            "object": dict,
        }

        expected_python_type = type_map.get(expected_type)
        if expected_python_type is None:
            return True  # Unknown type, skip validation

        return isinstance(value, expected_python_type)
