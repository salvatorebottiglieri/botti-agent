"""Tests for transcript metrics collection."""

from __future__ import annotations

from uuid import uuid4

from cortex.agentic.events import (
    ErrorEvent,
    ResponseDoneEvent,
    TextDeltaEvent,
    ThinkingEvent,
    ToolResultEvent,
    ToolStartEvent,
)
from cortex.eval.metrics import collect_metrics


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
