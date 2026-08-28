"""Task and suite metrics from the LoopEvent transcript.

Metrics are recorded from the loop's streaming events — what the
transcript carries today: ``ResponseDoneEvent.iterations``,
``ToolStartEvent`` tool names, ``ToolResultEvent`` success flags. Usage and
latency fields are intentionally not depended on (a sibling ticket adds
them to the transcript).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cortex.agentic.events import (
    LoopEvent,
    ResponseDoneEvent,
    ToolResultEvent,
    ToolStartEvent,
)


@dataclass
class TaskMetrics:
    """Per-task metrics collected from the loop transcript."""

    iterations: int = 0
    tools_used: list[str] = field(default_factory=list)  # unique, first-use order
    tool_calls: int = 0
    failed_calls: int = 0


@dataclass
class SuiteMetrics:
    """Aggregate metrics over a whole suite run."""

    task_count: int = 0
    pass_count: int = 0
    total_iterations: int = 0
    total_tool_calls: int = 0
    tools_used: list[str] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        """Fraction of tasks that passed (0.0 for an empty suite)."""
        return self.pass_count / self.task_count if self.task_count else 0.0

    @property
    def avg_iterations(self) -> float:
        """Mean iterations across tasks (0.0 for an empty suite)."""
        return self.total_iterations / self.task_count if self.task_count else 0.0


def collect_metrics(events: list[LoopEvent]) -> TaskMetrics:
    """Reduce a task's event transcript to :class:`TaskMetrics`."""
    metrics = TaskMetrics()
    seen: set[str] = set()
    for event in events:
        match event:
            case ResponseDoneEvent(iterations=iterations):
                metrics.iterations += iterations
            case ToolStartEvent(tool_name=name):
                metrics.tool_calls += 1
                if name not in seen:
                    seen.add(name)
                    metrics.tools_used.append(name)
            case ToolResultEvent(success=success):
                if not success:
                    metrics.failed_calls += 1
    return metrics
