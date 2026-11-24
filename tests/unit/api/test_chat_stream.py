"""Route-level SSE tests for POST /chat/stream (issue #17).

The seam is the full HTTP path: auth, session resolution, SSE framing and
streaming. The execution module and interaction service are fakes so the
loop itself is not exercised, except where a test drives the real AgentLoop
through the route to see the frames it actually produces.
"""

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from cortex.agentic.events import (
    AskUserEvent,
    ErrorEvent,
    ResponseDoneEvent,
    TextDeltaEvent,
    ThinkingEvent,
    ToolResultEvent,
    ToolStartEvent,
)
from cortex.agentic.loop import AgentLoop
from cortex.agentic.models import Context, Decision
from cortex.agentic.notices import PromotionNotices
from cortex.events.base import BaseEvent
from cortex.events.types import EventTypes
from cortex.execution.module import ExecutionModule
from cortex.rules.models import Rule
from cortex.sessions.models import Session, SessionState
from tests.unit.api.fakes import FakeInteractionService, FakeRuleService, build_client
from tests.unit.rules.fakes import InMemoryFeedbackQueue, RaisingQueue

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
        self.calls: list[tuple[UUID, str, int | None, bool, bool]] = []

    async def stream_chat(
        self,
        session_id: UUID,
        user_message: str,
        *,
        max_iterations: int | None = None,
        stream: bool = False,
        trace_enabled: bool = False,
    ):
        self.calls.append((session_id, user_message, max_iterations, stream, trace_enabled))
        if self._exc is not None:
            raise self._exc
        for event in self._events:
            yield event


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
        assert fake_exec.calls == [(session_id, "hi", 20, True, False)]

    def test_stream_flag_defaults_true_and_honors_false(self):
        """The `stream` flag reaches execution_module.stream_chat: defaults to
        True, and a request setting it False is forwarded as False."""
        session_id = uuid4()

        # Default (omitted) -> True
        fake_default = FakeExecutionModule([
            ResponseDoneEvent(session_id=session_id, message="ok", tools_used=[], iterations=0),
        ])
        client = build_client(
            fake_default, FakeInteractionService(existing_session=Session(id=session_id))
        )
        client.post(
            "/chat/stream",
            json={"message": "hi", "session_id": str(session_id)},
            headers=AUTH_HEADERS,
        )
        assert fake_default.calls[0][3] is True

        # Explicit stream=False -> False
        fake_off = FakeExecutionModule([
            ResponseDoneEvent(session_id=session_id, message="ok", tools_used=[], iterations=0),
        ])
        client = build_client(
            fake_off, FakeInteractionService(existing_session=Session(id=session_id))
        )
        client.post(
            "/chat/stream",
            json={"message": "hi", "session_id": str(session_id), "stream": False},
            headers=AUTH_HEADERS,
        )
        assert fake_off.calls[0][3] is False

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

    def test_ask_user_frame_carries_question_and_options(self):
        """An AskUserEvent maps to an `ask_user` frame with question + options,
        followed by the terminal `done` frame."""
        session_id = uuid4()
        fake_exec = FakeExecutionModule([
            ThinkingEvent(session_id=session_id, message="Need info."),
            AskUserEvent(
                session_id=session_id,
                question="Which file did you mean?",
                options=["a.txt", "b.txt"],
            ),
            ResponseDoneEvent(
                session_id=session_id,
                message="Which file did you mean?",
                tools_used=[],
                iterations=0,
            ),
        ])
        fake_interaction = FakeInteractionService(existing_session=Session(id=session_id))
        client = build_client(fake_exec, fake_interaction)

        response = client.post(
            "/chat/stream",
            json={"message": "open the file", "session_id": str(session_id)},
            headers=AUTH_HEADERS,
        )

        assert parse_sse(response.text) == [
            ("thinking", {"message": "Need info."}),
            ("ask_user", {
                "question": "Which file did you mean?",
                "options": ["a.txt", "b.txt"],
            }),
            ("done", {
                "final_message": "Which file did you mean?",
                "tool_calls": [],
                "iterations": 0,
            }),
        ]

    def test_ask_user_frame_options_default_empty(self):
        """An ask_user with no options serializes options as an empty list."""
        session_id = uuid4()
        fake_exec = FakeExecutionModule([
            AskUserEvent(session_id=session_id, question="A or B?"),
            ResponseDoneEvent(
                session_id=session_id, message="A or B?", tools_used=[], iterations=0
            ),
        ])
        fake_interaction = FakeInteractionService(existing_session=Session(id=session_id))
        client = build_client(fake_exec, fake_interaction)

        response = client.post(
            "/chat/stream",
            json={"message": "pick", "session_id": str(session_id)},
            headers=AUTH_HEADERS,
        )

        frames = parse_sse(response.text)
        assert frames[0] == ("ask_user", {"question": "A or B?", "options": []})

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
                rule_service=FakeRuleService(),
                feedback_queue=InMemoryFeedbackQueue(),
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

    def test_trace_enabled_session_flag_reaches_stream_chat(self):
        """The resolved session's trace_enabled is forwarded to
        execution_module.stream_chat: True for a traced session, False by
        default — capture only ever starts for opted-in sessions."""
        traced_id = uuid4()
        fake_traced = FakeExecutionModule([
            ResponseDoneEvent(session_id=traced_id, message="ok", tools_used=[], iterations=0),
        ])
        client = build_client(
            fake_traced,
            FakeInteractionService(
                existing_session=Session(id=traced_id, trace_enabled=True)
            ),
        )
        client.post(
            "/chat/stream",
            json={"message": "hi", "session_id": str(traced_id)},
            headers=AUTH_HEADERS,
        )
        assert fake_traced.calls[0][4] is True

        # Default (untraced) session -> False, and the stream still runs.
        plain_id = uuid4()
        fake_plain = FakeExecutionModule([
            ResponseDoneEvent(session_id=plain_id, message="ok", tools_used=[], iterations=0),
        ])
        client = build_client(
            fake_plain,
            FakeInteractionService(existing_session=Session(id=plain_id)),
        )
        client.post(
            "/chat/stream",
            json={"message": "hi", "session_id": str(plain_id)},
            headers=AUTH_HEADERS,
        )
        assert fake_plain.calls[0][4] is False


class TestChatStreamAttribution:
    """POST /chat/stream enqueues the finished turn for attribution (#172)."""

    def _drive(
        self,
        events: list[Any],
        *,
        rule_service: FakeRuleService | None = None,
        feedback_queue: InMemoryFeedbackQueue | None = None,
        message: str = "no, don't book without asking",
    ) -> tuple[Any, InMemoryFeedbackQueue, UUID]:
        session_id = uuid4()
        queue = feedback_queue or InMemoryFeedbackQueue()
        fake_exec = FakeExecutionModule(events)
        client = build_client(
            fake_exec,
            FakeInteractionService(existing_session=Session(id=session_id)),
            rule_service=rule_service,
            feedback_queue=queue,
        )
        response = client.post(
            "/chat/stream",
            json={"message": message, "session_id": str(session_id)},
            headers=AUTH_HEADERS,
        )
        return response, queue, session_id

    def test_one_row_before_the_done_frame_carries_the_turn(self):
        """The row is written before the done frame goes out, naming the rules
        read before the turn, the assistant text and the user's reply."""
        session_id = uuid4()
        rule = Rule(condition="the user asks to book", action="ask before booking")
        done = ResponseDoneEvent(
            session_id=session_id,
            message="of course, booking it",
            tools_used=[],
            iterations=1,
        )
        rule_service = FakeRuleService([rule])

        response, queue, turn_session = self._drive([done], rule_service=rule_service)

        assert parse_sse(response.text) == [
            (
                "done",
                {
                    "final_message": "of course, booking it",
                    "tool_calls": [],
                    "iterations": 1,
                },
            ),
        ]
        assert rule_service.calls == ["list_active"]
        assert len(queue.items) == 1
        row = queue.items[0]
        assert row.session_id == turn_session
        assert row.rule_ids == (rule.id,)
        assert row.assistant_turn == "of course, booking it"
        assert row.user_reply == "no, don't book without asking"

    def test_a_promotion_line_is_not_classified_as_the_assistant_turn(self):
        """The line is fixed copy, not the model's words: the classifier reads the
        assistant turn as the model produced it, yet the done frame still carries
        the line the user sees."""
        notice = "Rule confirmed: the user asks to book → ask before booking"
        rule = Rule(condition="the user asks to book", action="ask before booking")
        done = ResponseDoneEvent(
            session_id=uuid4(),
            message=f"{notice}\nof course, booking it",
            tools_used=[],
            iterations=1,
            notice=notice,
        )

        response, queue, _ = self._drive([done], rule_service=FakeRuleService([rule]))

        assert parse_sse(response.text)[-1][1]["final_message"] == done.message
        row = queue.items[0]
        assert row.assistant_turn == "of course, booking it"
        assert "Rule confirmed:" not in row.assistant_turn
        assert row.user_reply == "no, don't book without asking"

    def test_the_done_frame_opens_with_the_pending_line(self):
        """Witness: the frames above are hand-built, so they say nothing about
        what the loop puts on the wire. Here the real loop answers a turn with a
        pending line: the done frame's final message is the line and then the
        answer, the deltas already sent add up to it, and the queued turn stays
        the model's own text. A loop that drops the line from its done message
        leaves the line off the wire."""
        notice = "Rule confirmed: the user asks to book → ask before booking"
        answer = "of course, booking it"
        rule = Rule(condition="the user asks to book", action="ask before booking")
        session_id = uuid4()

        notices = PromotionNotices()
        asyncio.run(
            notices.handle(
                BaseEvent.create(
                    event_type=EventTypes.RULE_PROMOTED,
                    payload={"line": notice},
                    source_module="rules",
                )
            )
        )
        context_builder = MagicMock()
        context_builder.max_messages = 20
        context_builder.build = AsyncMock(return_value=Context(session_id=session_id))
        reasoner = MagicMock()
        reasoner.reason = AsyncMock(return_value=Decision.respond(answer))
        loop = AgentLoop(
            context_builder=context_builder,
            reasoner=reasoner,
            executor=MagicMock(),
            notices=notices,
            now=lambda: 0.0,
        )

        queue = InMemoryFeedbackQueue()
        client = build_client(
            ExecutionModule(agent_loop=loop),
            FakeInteractionService(existing_session=Session(id=session_id)),
            rule_service=FakeRuleService([rule]),
            feedback_queue=queue,
        )
        response = client.post(
            "/chat/stream",
            json={"message": "book a table", "session_id": str(session_id), "stream": False},
            headers=AUTH_HEADERS,
        )

        frames = parse_sse(response.text)
        assert frames[-1][0] == "done"
        final_message = frames[-1][1]["final_message"]
        assert final_message == f"{notice}\n{answer}"
        # The line the client already read as deltas is the frame's own prefix.
        assert "".join(d["delta"] for e, d in frames if e == "text") == final_message
        # ...and the line is not what the classifier is asked to attribute.
        assert queue.items[0].assistant_turn == answer

    def test_no_row_when_the_snapshot_was_empty(self):
        response, queue, _ = self._drive(
            [ResponseDoneEvent(session_id=uuid4(), message="ok", tools_used=[], iterations=0)],
            rule_service=FakeRuleService([]),
        )

        assert "event: done" in response.text
        assert queue.items == []

    def test_a_clarifying_question_is_attributed(self):
        """The done frame follows an ask_user, and that turn is still a turn: the
        reply exists and the rules were in context."""
        session_id = uuid4()
        rule = Rule(condition="the user asks to book", action="ask before booking")
        response, queue, _ = self._drive(
            [
                AskUserEvent(session_id=session_id, question="Which table?"),
                ResponseDoneEvent(
                    session_id=session_id, message="Which table?", tools_used=[], iterations=0
                ),
            ],
            rule_service=FakeRuleService([rule]),
        )

        assert [event for event, _ in parse_sse(response.text)] == ["ask_user", "done"]
        assert len(queue.items) == 1
        assert queue.items[0].rule_ids == (rule.id,)

    def test_no_row_on_an_error_frame(self):
        session_id = uuid4()
        rule = Rule(condition="the user asks to book", action="ask before booking")
        _, queue, _ = self._drive(
            [ErrorEvent(session_id=session_id, error="Max iterations exceeded", code="max_iterations")],
            rule_service=FakeRuleService([rule]),
        )

        assert queue.items == []

    def test_the_stream_is_unchanged_when_the_enqueue_raises(self):
        """A storage failure never reaches the user: enqueue_turn swallows it."""
        session_id = uuid4()
        rule = Rule(condition="the user asks to book", action="ask before booking")

        response, _, _ = self._drive(
            [ResponseDoneEvent(session_id=session_id, message="ok", tools_used=[], iterations=1)],
            rule_service=FakeRuleService([rule]),
            feedback_queue=RaisingQueue(),
        )

        assert response.status_code == 200
        assert parse_sse(response.text) == [
            ("done", {"final_message": "ok", "tool_calls": [], "iterations": 1}),
        ]

    def test_the_stream_is_unchanged_when_the_snapshot_read_raises(self):
        """A failing list_active costs attribution, never the turn."""
        session_id = uuid4()

        response, queue, _ = self._drive(
            [ResponseDoneEvent(session_id=session_id, message="ok", tools_used=[], iterations=1)],
            rule_service=FakeRuleService(exc=RuntimeError("database down")),
        )

        assert response.status_code == 200
        assert parse_sse(response.text) == [
            ("done", {"final_message": "ok", "tool_calls": [], "iterations": 1}),
        ]
        assert queue.items == []
