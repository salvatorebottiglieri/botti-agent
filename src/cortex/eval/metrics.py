"""Task and suite metrics from the LoopEvent transcript.

Metrics are recorded from the loop's streaming events — what the transcript
carries today: ``ResponseDoneEvent.iterations`` and the response ``usage``
and ``latency_ms`` it carries, ``ToolStartEvent`` tool names, and
``ToolResultEvent`` success flags. Per-question cost is derived from usage
tokens × per-model pricing (:func:`cortex.config.models.derive_cost`) when
pricing is supplied; per-question latency is the loop's wall time.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cortex.agentic.events import (
    LoopEvent,
    ResponseDoneEvent,
    ToolResultEvent,
    ToolStartEvent,
)
from cortex.config.models import ModelPricing, derive_cost


@dataclass
class TaskMetrics:
    """Per-task metrics collected from the loop transcript."""

    iterations: int = 0
    tools_used: list[str] = field(default_factory=list)  # unique, first-use order
    tool_calls: int = 0
    failed_calls: int = 0
    cost: float = 0.0  # USD, usage tokens × pricing (0 when pricing unknown)
    latency_ms: float = 0.0  # loop wall time across the task's responses


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


def collect_metrics(
    events: list[LoopEvent], pricing: ModelPricing | None = None
) -> TaskMetrics:
    """Reduce a task's event transcript to :class:`TaskMetrics`.

    ``cost`` estimates each response's token usage at ``pricing``; pass the
    suite model's pricing (settings ``llm_pricing``) to get nonzero
    per-question cost. ``latency_ms`` sums the per-response loop wall time
    carried by ``ResponseDoneEvent``.
    """
    metrics = TaskMetrics()
    seen: set[str] = set()
    cost = 0.0
    latency_ms = 0.0
    for event in events:
        match event:
            case ResponseDoneEvent(
                iterations=iterations, usage=usage, latency_ms=latency
            ):
                metrics.iterations += iterations
                if usage is not None and pricing is not None:
                    cost += derive_cost(usage, pricing)
                if latency is not None:
                    latency_ms += latency
            case ToolStartEvent(tool_name=name):
                metrics.tool_calls += 1
                if name not in seen:
                    seen.add(name)
                    metrics.tools_used.append(name)
            case ToolResultEvent(success=success):
                if not success:
                    metrics.failed_calls += 1
    metrics.cost = cost
    metrics.latency_ms = latency_ms
    return metrics
