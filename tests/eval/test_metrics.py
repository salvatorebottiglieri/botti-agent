"""Tests for transcript metrics collection."""

from __future__ import annotations

from uuid import uuid4

import pytest

from cortex.agentic.events import (
    ErrorEvent,
    ResponseDoneEvent,
    TextDeltaEvent,
    ThinkingEvent,
    ToolResultEvent,
    ToolStartEvent,
)
from cortex.config.models import ModelPricing
from cortex.eval.metrics import collect_metrics
from cortex.llm.models import UsageStats


class TestCostAndLatencyMetrics:
    """Per-question cost and latency come from ResponseDoneEvent usage/timing."""

    def test_cost_and_latency_accumulate_across_done_events(self):
        """Cost (usage × pricing) and latency sum over a task's responses."""
        sid = _sid()
        usage = UsageStats(prompt_tokens=1_000_000, completion_tokens=500_000)
        events = [
            ResponseDoneEvent(
                session_id=sid,
                message="a",
                iterations=1,
                usage=usage,
                latency_ms=150.0,
            ),
            ResponseDoneEvent(
                session_id=sid,
                message="b",
                iterations=1,
                usage=usage,
                latency_ms=50.0,
            ),
        ]
        pricing = ModelPricing(input_per_mtok=1.0, output_per_mtok=2.0)
        metrics = collect_metrics(events, pricing=pricing)
        assert metrics.cost_usd == pytest.approx(4.0)
        assert metrics.latency_ms == pytest.approx(200.0)
        assert metrics.usage == usage + usage

    def test_no_pricing_means_zero_cost(self):
        """Without pricing the transcript yields zero cost."""
        sid = _sid()
        usage = UsageStats(prompt_tokens=100, completion_tokens=50)
        events = [ResponseDoneEvent(session_id=sid, message="a", usage=usage)]
        metrics = collect_metrics(events)
        assert metrics.cost_usd == 0.0
        assert metrics.usage == usage

    def test_missing_usage_and_latency_are_ignored(self):
        """Done events without usage or latency contribute nothing."""
        sid = _sid()
        events = [ResponseDoneEvent(session_id=sid, message="a")]
        metrics = collect_metrics(events)
        assert metrics.cost_usd == 0.0
        assert metrics.latency_ms is None
        assert metrics.usage is None


def _sid() -> str:
    return str(uuid4())


class TestCollectMetrics:
    """Metrics come from the LoopEvent transcript only (what it carries today)."""

    def test_empty_transcript(self):
        """No events -> zeroed metrics."""
        metrics = collect_metrics([])
        assert metrics.iterations == 0
        assert metrics.tools_used == []
        assert metrics.tool_calls == 0
        assert metrics.failed_calls == 0

    def test_counts_iterations_from_done_event(self):
        """iterations come from ResponseDoneEvent, summed across turns."""
        events = [
            ResponseDoneEvent(session_id=_sid(), message="a", iterations=2),
            ResponseDoneEvent(session_id=_sid(), message="b", iterations=3),
        ]
        metrics = collect_metrics(events)
        assert metrics.iterations == 5

    def test_counts_tool_calls_in_first_use_order(self):
        """tools_used is the unique tool names in first-use order."""
        sid = _sid()
        events = [
            ToolStartEvent(session_id=sid, tool_name="grep", tool_call_id="c1"),
            ToolStartEvent(session_id=sid, tool_name="file_read", tool_call_id="c2"),
            ToolStartEvent(session_id=sid, tool_name="grep", tool_call_id="c3"),
        ]
        metrics = collect_metrics(events)
        assert metrics.tool_calls == 3
        assert metrics.tools_used == ["grep", "file_read"]

    def test_counts_failed_tool_calls(self):
        """ToolResultEvent(success=False) counts as a failed call."""
        sid = _sid()
        events = [
            ToolResultEvent(
                session_id=sid, tool_name="shell", tool_call_id="c1", success=True
            ),
            ToolResultEvent(
                session_id=sid, tool_name="file_write", tool_call_id="c2", success=False
            ),
        ]
        metrics = collect_metrics(events)
        assert metrics.failed_calls == 1
        assert metrics.tool_calls == 0  # only starts count calls

    def test_ignores_progress_and_error_events(self):
        """thinking/text/error events contribute nothing."""
        sid = _sid()
        events = [
            ThinkingEvent(session_id=sid, message="hmm"),
            TextDeltaEvent(session_id=sid, delta="hi"),
            ErrorEvent(session_id=sid, error="boom"),
            ToolStartEvent(session_id=sid, tool_name="shell", tool_call_id="c1"),
            ResponseDoneEvent(session_id=sid, message="hi", iterations=1),
        ]
        metrics = collect_metrics(events)
        assert metrics.iterations == 1
        assert metrics.tool_calls == 1
        assert metrics.tools_used == ["shell"]
