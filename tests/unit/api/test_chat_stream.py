"""Route-level SSE tests for POST /chat/stream (issue #17).

The seam is the full HTTP path: auth, session resolution, SSE framing and
streaming. The execution module and interaction service are fakes so the
loop itself is never exercised.
"""

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from cortex.agentic.events import (
    ErrorEvent,
    ResponseDoneEvent,
    TextDeltaEvent,
    ThinkingEvent,
    ToolResultEvent,
    ToolStartEvent,
)
from cortex.api.auth import get_api_key
from cortex.api.dependencies import get_execution_module, get_interaction_service
from cortex.main import create_app
from cortex.sessions.models import Session, SessionState

AUTH_HEADERS = {"Authorization": "Bearer dummy-key"}


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse an SSE body into (event, data) frames."""
    frames: list[tuple[str, dict[str, Any]]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        event: str | None = None
        data: str | None = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = line.removeprefix("data: ")
        assert event is not None, f"SSE block missing event name: {block!r}"
        assert data is not None, f"SSE block missing data: {block!r}"
        frames.append((event, json.loads(data)))
    return frames


class FakeExecutionModule:
    """Scripted stream_chat: yields pre-built LoopEvents, or raises."""

    def __init__(
        self,
        events: list[Any] | None = None,
        exc: Exception | None = None,
    ):
        self._events = events or []
        self._exc = exc
        self.calls: list[tuple[UUID, str, int | None]] = []

    async def stream_chat(
        self,
        session_id: UUID,
        user_message: str,
        *,
        max_iterations: int | None = None,
    ):
        self.calls.append((session_id, user_message, max_iterations))
        if self._exc is not None:
            raise self._exc
        for event in self._events:
            yield event


class FakeInteractionService:
    """Records session lookups/creations; serves a scripted existing session."""

    def __init__(self, existing_session: Session | None = None):
        self._existing_session = existing_session
        self.created_sessions: list[Session] = []

    async def get_session(self, session_id: UUID) -> Session | None:
        if self._existing_session is not None and self._existing_session.id == session_id:
            return self._existing_session
        return None

    async def get_or_create_session(self, session_id: UUID | None) -> Session:
        if session_id is not None:
            existing = await self.get_session(session_id)
            if existing is not None and existing.state != SessionState.ENDED:
                return existing
        session = Session()
        self.created_sessions.append(session)
        return session


def build_client(
    execution_module: FakeExecutionModule,
    interaction_service: FakeInteractionService,
    *,
    raise_server_exceptions: bool = True,
) -> TestClient:
    """App with the three chat dependencies overridden by fakes."""
    settings = MagicMock()
    settings.version = "0.1.0"
    with patch("cortex.main.get_settings", return_value=settings):
        app = create_app()
    app.dependency_overrides[get_api_key] = lambda: "dummy-key"
    app.dependency_overrides[get_execution_module] = lambda: execution_module
    app.dependency_overrides[get_interaction_service] = lambda: interaction_service
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


class TestChatStreamSSE:
    """POST /chat/stream maps LoopEvents to SSE frames at the route seam."""

    def test_respond_script_frames_in_order(self):
        """RESPOND script streams thinking, text, done in order with
        documented payload field names."""
        session_id = uuid4()
        fake_exec = FakeExecutionModule([
            ThinkingEvent(session_id=session_id, message="Let me reason."),
            TextDeltaEvent(session_id=session_id, delta="Hello there!"),
            ResponseDoneEvent(
                session_id=session_id,
                message="Hello there!",
                tools_used=[],
                iterations=1,
            ),
        ])
        fake_interaction = FakeInteractionService(existing_session=Session(id=session_id))
        client = build_client(fake_exec, fake_interaction)

        response = client.post(
            "/chat/stream",
            json={"message": "hi", "session_id": str(session_id)},
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["connection"] == "keep-alive"
        assert response.headers["x-accel-buffering"] == "no"
        assert parse_sse(response.text) == [
            ("thinking", {"message": "Let me reason."}),
            ("text", {"delta": "Hello there!"}),
            ("done", {"final_message": "Hello there!", "tool_calls": [], "iterations": 1}),
        ]
        assert fake_exec.calls == [(session_id, "hi", 20)]

    def test_multiple_text_deltas_yield_one_frame_each_in_order(self):
        """Two TextDeltaEvents produce two text frames in order — text
        streams as it accumulates, not just at the end."""
        session_id = uuid4()
        fake_exec = FakeExecutionModule([
            TextDeltaEvent(session_id=session_id, delta="Hello "),
            TextDeltaEvent(session_id=session_id, delta="world!"),
            ResponseDoneEvent(
                session_id=session_id,
                message="Hello world!",
                tools_used=[],
                iterations=1,
            ),
        ])
        fake_interaction = FakeInteractionService(existing_session=Session(id=session_id))
        client = build_client(fake_exec, fake_interaction)

        response = client.post(
            "/chat/stream",
            json={"message": "hi", "session_id": str(session_id)},
            headers=AUTH_HEADERS,
        )

        assert parse_sse(response.text) == [
            ("text", {"delta": "Hello "}),
            ("text", {"delta": "world!"}),
            ("done", {"final_message": "Hello world!", "tool_calls": [], "iterations": 1}),
        ]

    def test_tool_round_interleaves_start_done_pairs(self):
        """ToolStartEvent/ToolResultEvent interleave as paired frames carrying
        success/output/error/execution_time_ms, before text and done."""
        session_id = uuid4()
        fake_exec = FakeExecutionModule([
            ThinkingEvent(session_id=session_id, message="Looking it up."),
            ToolStartEvent(session_id=session_id, tool_name="web_search", tool_call_id="call_1"),
            ToolResultEvent(
                session_id=session_id,
                tool_name="web_search",
                tool_call_id="call_1",
                success=True,
                output="results",
                error=None,
                execution_time_ms=12.5,
            ),
            ToolStartEvent(session_id=session_id, tool_name="calculator", tool_call_id="call_2"),
            ToolResultEvent(
                session_id=session_id,
                tool_name="calculator",
                tool_call_id="call_2",
                success=False,
                output=None,
                error="division by zero",
                execution_time_ms=3.0,
            ),
            TextDeltaEvent(session_id=session_id, delta="Found it."),
            ResponseDoneEvent(
                session_id=session_id,
                message="Found it.",
                tools_used=["web_search", "calculator"],
                iterations=2,
            ),
        ])
        fake_interaction = FakeInteractionService(existing_session=Session(id=session_id))
        client = build_client(fake_exec, fake_interaction)

        response = client.post(
            "/chat/stream",
            json={"message": "find it", "session_id": str(session_id)},
            headers=AUTH_HEADERS,
        )

        assert parse_sse(response.text) == [
            ("thinking", {"message": "Looking it up."}),
            (
                "tool_start",
                {"tool_name": "web_search", "tool_call_id": "call_1"},
            ),
            (
                "tool_done",
                {
                    "tool_name": "web_search",
                    "tool_call_id": "call_1",
                    "success": True,
                    "output": "results",
                    "error": None,
                    "execution_time_ms": 12.5,
                },
            ),
            (
                "tool_start",
                {"tool_name": "calculator", "tool_call_id": "call_2"},
            ),
            (
                "tool_done",
                {
                    "tool_name": "calculator",
                    "tool_call_id": "call_2",
                    "success": False,
                    "output": None,
                    "error": "division by zero",
                    "execution_time_ms": 3.0,
                },
            ),
            ("text", {"delta": "Found it."}),
            (
                "done",
                {
                    "final_message": "Found it.",
                    "tool_calls": ["web_search", "calculator"],
                    "iterations": 2,
                },
            ),
        ]

    def test_done_frame_renames_never_drops(self):
        """The done frame carries final_message/tool_calls/iterations, never
        the model field names message/tools_used, and never omits a field."""
        session_id = uuid4()
        fake_exec = FakeExecutionModule([
            ResponseDoneEvent(
                session_id=session_id,
                message="Done.",
                tools_used=["web_search"],
                iterations=3,
            ),
        ])
        fake_interaction = FakeInteractionService(existing_session=Session(id=session_id))
        client = build_client(fake_exec, fake_interaction)

        response = client.post(
            "/chat/stream",
            json={"message": "go", "session_id": str(session_id)},
            headers=AUTH_HEADERS,
        )

        done_payload = parse_sse(response.text)[-1][1]
        assert done_payload == {
            "final_message": "Done.",
            "tool_calls": ["web_search"],
            "iterations": 3,
        }
        assert "message" not in done_payload
        assert "tools_used" not in done_payload

    def test_missing_session_returns_404_with_zero_frames(self):
        """A provided session_id that does not exist is an HTTP 404 before
        any frame — stream_chat is never invoked."""
        fake_exec = FakeExecutionModule([
            TextDeltaEvent(session_id=uuid4(), delta="never"),
        ])
        fake_interaction = FakeInteractionService()  # no existing session
        client = build_client(fake_exec, fake_interaction)

        response = client.post(
            "/chat/stream",
            json={"message": "hi", "session_id": str(uuid4())},
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 404
        assert "event: " not in response.text
        assert fake_exec.calls == []

    def test_absent_session_creates_and_streams_normally(self):
        """An absent session_id exercises the creation path; frames stream
        with the newly created session id."""
        fake_exec = FakeExecutionModule([
            ThinkingEvent(session_id=uuid4(), message="Fresh session."),
            ResponseDoneEvent(
                session_id=uuid4(),
                message="Done.",
                tools_used=[],
                iterations=1,
            ),
        ])
        fake_interaction = FakeInteractionService()
        client = build_client(fake_exec, fake_interaction)

        response = client.post(
            "/chat/stream",
            json={"message": "hi"},
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        assert len(fake_interaction.created_sessions) == 1
        created = fake_interaction.created_sessions[0]
        assert fake_exec.calls[0][0] == created.id
        assert fake_exec.calls[0][2] == 20  # max_iterations default
        assert parse_sse(response.text) == [
            ("thinking", {"message": "Fresh session."}),
            ("done", {"final_message": "Done.", "tool_calls": [], "iterations": 1}),
        ]

    def test_provided_ended_session_creates_fresh_session_and_streams(self):
        """A provided-but-ENDED session_id is not handed back: the policy
        treats ENDED as absent, so get_or_create_session creates a fresh
        session and the route runs the stream against it — never the ENDED
        id, and never a 404 (the session does exist)."""
        ended_id = uuid4()
        fake_exec = FakeExecutionModule([
            TextDeltaEvent(session_id=ended_id, delta="Fresh session."),
            ResponseDoneEvent(
                session_id=ended_id,
                message="Done.",
                tools_used=[],
                iterations=1,
            ),
        ])
        fake_interaction = FakeInteractionService(
            existing_session=Session(id=ended_id, state=SessionState.ENDED)
        )
        client = build_client(fake_exec, fake_interaction)

        response = client.post(
            "/chat/stream",
            json={"message": "hi", "session_id": str(ended_id)},
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200  # no 404: the ENDED session exists
        assert len(fake_interaction.created_sessions) == 1
        created = fake_interaction.created_sessions[0]
        assert created.id != ended_id
        # the stream runs against the fresh session, not the ENDED id
        assert fake_exec.calls[0][0] == created.id
        assert fake_exec.calls[0][2] == 20  # max_iterations default
        assert parse_sse(response.text) == [
            ("text", {"delta": "Fresh session."}),
            ("done", {"final_message": "Done.", "tool_calls": [], "iterations": 1}),
        ]

    def test_error_event_is_terminal_and_propagates_max_iterations_code(self):
        """An ErrorEvent yields exactly one error frame with code
        'max_iterations'; the stream ends — events scripted after the error
        never surface."""
        session_id = uuid4()
        fake_exec = FakeExecutionModule([
            ErrorEvent(
                session_id=session_id,
                error="Max iterations exceeded",
                code="max_iterations",
            ),
            TextDeltaEvent(session_id=session_id, delta="must not stream"),
            ResponseDoneEvent(session_id=session_id, message="must not stream"),
        ])
        fake_interaction = FakeInteractionService(existing_session=Session(id=session_id))
        client = build_client(fake_exec, fake_interaction)

        response = client.post(
            "/chat/stream",
            json={"message": "hi", "session_id": str(session_id)},
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        assert parse_sse(response.text) == [
            ("error", {"error": "Max iterations exceeded", "code": "max_iterations"}),
        ]

    def test_error_event_passes_null_code_through(self):
        """An ErrorEvent with code None must put null on the wire — the
        documented contract is 'max_iterations' for MaxIterationsError, null
        otherwise, and the arm passes event.code through unchanged."""
        session_id = uuid4()
        fake_exec = FakeExecutionModule([
            ErrorEvent(
                session_id=session_id,
                error="Tool exhausted retries",
                code=None,
            ),
            TextDeltaEvent(session_id=session_id, delta="must not stream"),
        ])
        fake_interaction = FakeInteractionService(existing_session=Session(id=session_id))
        client = build_client(fake_exec, fake_interaction)

        response = client.post(
            "/chat/stream",
            json={"message": "hi", "session_id": str(session_id)},
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        assert parse_sse(response.text) == [
            ("error", {"error": "Tool exhausted retries", "code": None}),
        ]

    def test_unexpected_exception_yields_error_null_frame_and_reraises(self):
        """An exception escaping stream_chat without an ErrorEvent yields an
        error{code: null} frame, then the exception re-raises (surfaces out
        of the stream, so it is logged by the server — never silent).

        The frame itself is asserted at the route-handler seam: buffering
        ASGI clients (Starlette TestClient, httpx ASGITransport) cannot
        deliver body chunks sent before a mid-stream app exception, so the
        partial body is unobservable through the HTTP transport.
        """
        session_id = uuid4()
        fake_exec = FakeExecutionModule(exc=RuntimeError("boom"))
        fake_interaction = FakeInteractionService(existing_session=Session(id=session_id))
        payload = {"message": "hi", "session_id": str(session_id)}

        # The error{code: null} frame is yielded before the exception
        # re-raises out of the stream.
        from cortex.api.routes.chat import chat_stream
        from cortex.api.schemas import ChatRequest

        response = asyncio.run(
            chat_stream(
                ChatRequest(message="hi", session_id=session_id),
                key="dummy-key",
                interaction_service=fake_interaction,
                execution_module=fake_exec,
            )
        )

        async def drain() -> list[str]:
            frames: list[str] = []
            with pytest.raises(RuntimeError, match="boom"):
                async for chunk in response.body_iterator:
                    frames.append(chunk)
            return frames

        assert parse_sse("".join(asyncio.run(drain()))) == [
            ("error", {"error": "boom", "code": None}),
        ]

        # Through the full HTTP path the exception surfaces out of the
        # stream (never swallowed into a silent end). It raises at the
        # stream entry, before httpx constructs a Response, so no body is
        # iterated.
        surfacing_client = build_client(fake_exec, fake_interaction, raise_server_exceptions=True)
        with pytest.raises(RuntimeError, match="boom"):
            with surfacing_client.stream(
                "POST", "/chat/stream", json=payload, headers=AUTH_HEADERS
            ):
                pass
