"""Tests for agentic loop events (ADR-0002 streaming seam contract)."""

import json
from dataclasses import is_dataclass
from uuid import UUID, uuid4

import pytest

from cortex.agentic.events import (
    ErrorEvent,
    LoopEvent,
    ResponseDoneEvent,
    TextDeltaEvent,
    ThinkingEvent,
    ToolResultEvent,
    ToolStartEvent,
)

WIRE_NAMES = {
    ThinkingEvent: "thinking",
    TextDeltaEvent: "text",
    ToolStartEvent: "tool_start",
    ToolResultEvent: "tool_done",
    ResponseDoneEvent: "done",
    ErrorEvent: "error",
}


def _sample_events(session_id: UUID) -> list[LoopEvent]:
    """One fully-populated instance of every event type."""
    return [
        ThinkingEvent(session_id=session_id, message="reasoning step"),
        TextDeltaEvent(session_id=session_id, delta="Hello"),
        ToolStartEvent(
            session_id=session_id, tool_name="web_search", tool_call_id="call_abc123"
        ),
        ToolResultEvent(
            session_id=session_id,
            tool_name="web_search",
            tool_call_id="call_abc123",
            success=True,
            output="search results",
            execution_time_ms=12.5,
        ),
        ResponseDoneEvent(
            session_id=session_id,
            message="All done",
            tools_used=["web_search", "calculator"],
            iterations=3,
        ),
        ErrorEvent(session_id=session_id, error="boom", code="max_iterations"),
    ]


class TestLoopEvent:
    """Tests for the LoopEvent base class."""

    def test_is_dataclass(self):
        assert is_dataclass(LoopEvent)

    def test_requires_session_id(self):
        with pytest.raises(TypeError):
            LoopEvent()

    def test_base_event_type_is_empty(self):
        assert LoopEvent.event_type == ""


class TestEventContract:
    """Shared contract across all six event types."""

    def test_all_event_types_are_dataclasses(self):
        for event_cls in WIRE_NAMES:
            assert is_dataclass(event_cls)

    def test_all_event_types_subclass_loop_event(self):
        for event_cls in WIRE_NAMES:
            assert issubclass(event_cls, LoopEvent)

    def test_all_instances_are_loop_events(self):
        session_id = uuid4()
        for event in _sample_events(session_id):
            assert isinstance(event, LoopEvent)

    def test_every_event_requires_session_id(self):
        for event_cls in WIRE_NAMES:
            with pytest.raises(TypeError):
                event_cls()  # type: ignore[call-arg]

    def test_wire_names_match_vocabulary(self):
        assert {cls.event_type for cls in WIRE_NAMES} == {
            "thinking",
            "text",
            "tool_start",
            "tool_done",
            "done",
            "error",
        }
        for cls, name in WIRE_NAMES.items():
            assert cls.event_type == name

    def test_event_type_accessible_on_instance(self):
        session_id = uuid4()
        for event in _sample_events(session_id):
            assert event.event_type == WIRE_NAMES[type(event)]

    def test_event_type_not_an_instance_field(self):
        """ClassVar must not materialize per-instance state in __dict__."""
        session_id = uuid4()
        for event in _sample_events(session_id):
            assert "event_type" not in vars(event)

    def test_to_dict_includes_event_type_and_all_instance_fields(self):
        session_id = uuid4()
        for event in _sample_events(session_id):
            payload = event.to_dict()
            assert payload["event_type"] == event.event_type
            for name in vars(event):
                assert name in payload

    def test_reconstruct_from_to_dict_payload(self):
        """to_dict payloads are plain-JSON-serializable and rebuild events."""
        session_id = uuid4()
        for event in _sample_events(session_id):
            payload = event.to_dict()
            json.dumps(payload)  # no default=str: JSON-ready output
            payload.pop("event_type")
            payload["session_id"] = UUID(payload["session_id"])
            assert type(event)(**payload) == event

    def test_json_roundtrip_preserves_all_fields(self):
        session_id = uuid4()
        for event in _sample_events(session_id):
            payload = json.dumps(event.to_dict(), default=str)
            parsed = json.loads(payload)
            assert parsed["event_type"] == event.event_type
            for name, value in vars(event).items():
                if isinstance(value, UUID):
                    assert UUID(parsed[name]) == value
                else:
                    assert parsed[name] == value


class TestThinkingEvent:
    """Tests for ThinkingEvent."""

    def test_wire_name(self):
        event = ThinkingEvent(session_id=uuid4(), message="planning")
        assert event.event_type == "thinking"

    def test_message_field(self):
        event = ThinkingEvent(session_id=uuid4(), message="planning")
        assert event.message == "planning"

    def test_to_dict(self):
        event = ThinkingEvent(session_id=uuid4(), message="planning")
        assert event.to_dict() == {
            "event_type": "thinking",
            "session_id": str(event.session_id),
            "message": "planning",
        }


class TestTextDeltaEvent:
    """Tests for TextDeltaEvent."""

    def test_wire_name(self):
        event = TextDeltaEvent(session_id=uuid4(), delta="Hello")
        assert event.event_type == "text"

    def test_delta_field(self):
        event = TextDeltaEvent(session_id=uuid4(), delta="Hello")
        assert event.delta == "Hello"


class TestToolStartEvent:
    """Tests for ToolStartEvent."""

    def test_wire_name(self):
        event = ToolStartEvent(
            session_id=uuid4(), tool_name="web_search", tool_call_id="call_abc123"
        )
        assert event.event_type == "tool_start"

    def test_fields(self):
        event = ToolStartEvent(
            session_id=uuid4(), tool_name="web_search", tool_call_id="call_abc123"
        )
        assert event.tool_name == "web_search"
        assert event.tool_call_id == "call_abc123"


class TestToolResultEvent:
    """Tests for ToolResultEvent."""

    def test_wire_name(self):
        event = ToolResultEvent(
            session_id=uuid4(),
            tool_name="web_search",
            tool_call_id="call_abc123",
            success=True,
        )
        assert event.event_type == "tool_done"

    def test_execution_time_ms_defaults_to_none(self):
        event = ToolResultEvent(
            session_id=uuid4(),
            tool_name="web_search",
            tool_call_id="call_abc123",
            success=True,
        )
        assert event.execution_time_ms is None

    def test_output_defaults_to_none(self):
        event = ToolResultEvent(
            session_id=uuid4(),
            tool_name="web_search",
            tool_call_id="call_abc123",
            success=True,
        )
        assert event.output is None

    def test_error_defaults_to_none(self):
        event = ToolResultEvent(
            session_id=uuid4(),
            tool_name="web_search",
            tool_call_id="call_abc123",
            success=True,
        )
        assert event.error is None

    def test_success_with_output(self):
        event = ToolResultEvent(
            session_id=uuid4(),
            tool_name="web_search",
            tool_call_id="call_abc123",
            success=True,
            output="search results",
        )
        assert event.success is True
        assert event.output == "search results"
        assert event.error is None

    def test_failure_with_error(self):
        event = ToolResultEvent(
            session_id=uuid4(),
            tool_name="web_search",
            tool_call_id="call_abc123",
            success=False,
            error="network timeout",
        )
        assert event.success is False
        assert event.error == "network timeout"
        assert event.output is None

    def test_execution_time_ms_float(self):
        event = ToolResultEvent(
            session_id=uuid4(),
            tool_name="web_search",
            tool_call_id="call_abc123",
            success=True,
            execution_time_ms=12.5,
        )
        assert event.execution_time_ms == 12.5


class TestResponseDoneEvent:
    """Tests for ResponseDoneEvent."""

    def test_wire_name(self):
        event = ResponseDoneEvent(session_id=uuid4(), message="Done")
        assert event.event_type == "done"

    def test_tools_used_defaults_to_empty_list(self):
        event = ResponseDoneEvent(session_id=uuid4(), message="Done")
        assert event.tools_used == []

    def test_iterations_defaults_to_zero(self):
        event = ResponseDoneEvent(session_id=uuid4(), message="Done")
        assert event.iterations == 0

    def test_tools_used_holds_tool_names(self):
        event = ResponseDoneEvent(
            session_id=uuid4(), message="Done", tools_used=["web_search", "calculator"]
        )
        assert event.tools_used == ["web_search", "calculator"]

    def test_default_tools_used_is_not_shared(self):
        first = ResponseDoneEvent(session_id=uuid4(), message="Done")
        second = ResponseDoneEvent(session_id=uuid4(), message="Done")
        first.tools_used.append("web_search")
        assert second.tools_used == []


class TestErrorEvent:
    """Tests for ErrorEvent."""

    def test_wire_name(self):
        event = ErrorEvent(session_id=uuid4(), error="boom")
        assert event.event_type == "error"

    def test_error_required(self):
        with pytest.raises(TypeError):
            ErrorEvent(session_id=uuid4())

    def test_code_defaults_to_none(self):
        event = ErrorEvent(session_id=uuid4(), error="boom")
        assert event.code is None

    def test_code_max_iterations_reserved(self):
        event = ErrorEvent(session_id=uuid4(), error="boom", code="max_iterations")
        assert event.code == "max_iterations"
