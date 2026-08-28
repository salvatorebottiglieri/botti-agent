"""Task and suite metrics from the LoopEvent transcript.

Metrics are recorded from the loop's streaming events — what the
transcript carries today: ``ResponseDoneEvent.iterations``,
``ToolStartEvent`` tool names, ``ToolResultEvent`` success flags. The done
event also carries token usage and latency, so tasks additionally record
``usage`` (accumulated across turns), ``latency_ms``, and — when the caller
pins a :class:`ModelPricing` — the USD cost derived from usage
(:func:`cortex.config.models.derive_cost`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from cortex.agentic.events import (
    LoopEvent,
    ResponseDoneEvent,
    ToolResultEvent,
    ToolStartEvent,
)
from cortex.config.models import ModelPricing, derive_cost

if TYPE_CHECKING:
    from cortex.llm.models import UsageStats


@dataclass
class TaskMetrics:
    """Per-task metrics collected from the loop transcript."""

    iterations: int = 0
    tools_used: list[str] = field(default_factory=list)  # unique, first-use order
    tool_calls: int = 0
    failed_calls: int = 0
    usage: UsageStats | None = None
    latency_ms: float | None = None
    cost_usd: float = 0.0


@dataclass
class SuiteMetrics:
    """Aggregate metrics over a whole suite run."""

    task_count: int = 0
    pass_count: int = 0
    total_iterations: int = 0
    total_tool_calls: int = 0
    tools_used: list[str] = field(default_factory=list)
    total_latency_ms: float = 0.0
    total_cost_usd: float = 0.0

    @property
    def pass_rate(self) -> float:
        """Fraction of tasks that passed (0.0 for an empty suite)."""
        return self.pass_count / self.task_count if self.task_count else 0.0

    @property
    def avg_iterations(self) -> float:
        """Mean iterations across tasks (0.0 for an empty suite)."""
        return self.total_iterations / self.task_count if self.task_count else 0.0


def collect_metrics(events: list[LoopEvent], pricing: ModelPricing | None = None) -> TaskMetrics:
    """Reduce a task's event transcript to :class:`TaskMetrics`.

    Args:
        events: The task's loop transcript.
        pricing: Per-model pricing used to derive ``cost_usd`` from the
            accumulated token usage. When None (no pricing pinned), cost is
            left at 0.0 while raw usage is still recorded.
    """
    metrics = TaskMetrics()
    seen: set[str] = set()
    for event in events:
        match event:
            case ResponseDoneEvent(iterations=iterations, usage=usage, latency_ms=latency_ms):
                metrics.iterations += iterations
                if usage is not None:
                    metrics.usage = usage if metrics.usage is None else metrics.usage + usage
                    if pricing is not None:
                        metrics.cost_usd += derive_cost(usage, pricing)
                if latency_ms is not None:
                    metrics.latency_ms = (metrics.latency_ms or 0.0) + latency_ms
            case ToolStartEvent(tool_name=name):
                metrics.tool_calls += 1
                if name not in seen:
                    seen.add(name)
                    metrics.tools_used.append(name)
            case ToolResultEvent(success=success):
                if not success:
                    metrics.failed_calls += 1
    return metrics


def compute_pass_k(results_by_task: dict[str, list[bool]], k: int) -> float:
    """Fraction of tasks whose first k trials all passed (pass^k consistency).

    pass^k is the probability that all k trials of a task succeed — the
    consistency metric for loop tasks (CONTEXT.md: pass^k). v1 runs each
    task once, so over a single-run result (one trial per task) pass^1
    equals the suite pass rate; the k>=3 phase feeds repeated suite runs
    into this helper. run_suite itself stays single-trial.

    Args:
        results_by_task: Task name to its per-trial pass/fail outcomes —
            one entry per run, in run order.
        k: Number of consecutive trials required for a task to pass (>= 1).

    Returns:
        Fraction of tasks whose first k trials all passed. A task with
        fewer than k trials cannot demonstrate pass^k and counts as not
        passing; an empty mapping yields 0.0 (mirroring ``pass_rate`` on
        an empty suite).
    """
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")
    if not results_by_task:
        return 0.0
    passing = sum(
        1
        for trials in results_by_task.values()
        if len(trials) >= k and all(trials[:k])
    )
    return passing / len(results_by_task)
