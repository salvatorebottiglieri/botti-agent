"""Route-level tests for attribution on POST /chat (#172).

The non-streaming twin of ``test_chat_stream.py``'s attribution class: the
turn's rules are read before the turn and one pending row is written after the
answer, and neither read nor write can refuse the response.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from uuid import UUID, uuid4

from cortex.api.routes.chat import _model_text
from cortex.rules.models import Rule
from cortex.sessions.models import Session
from tests.unit.api.fakes import FakeInteractionService, FakeRuleService, build_client
from tests.unit.rules.fakes import InMemoryFeedbackQueue, RaisingQueue

AUTH_HEADERS = {"Authorization": "Bearer dummy-key"}


class FakeExecutionModule:
    """A one-turn run_chat: returns a scripted answer, or raises."""

    def __init__(
        self,
        message: str = "of course, booking it",
        exc: Exception | None = None,
        calls: list[str] | None = None,
        notice: str | None = None,
    ):
        self._message = message
        self._exc = exc
        self._calls = calls
        self._notice = notice

    async def run_chat(
        self, session_id: UUID, user_message: str, *, max_iterations: int = 20
    ) -> SimpleNamespace:
        if self._calls is not None:
            self._calls.append("run_chat")
        if self._exc is not None:
            raise self._exc
        return SimpleNamespace(
            message=self._message, iterations=1, tools_used=[], notice=self._notice
        )


@dataclass
class ChatCall:
    """What one POST /chat produced: the response and the session it ran on."""

    response: object
    session_id: UUID


def _post(
    *,
    execution_module: FakeExecutionModule | None = None,
    rule_service: FakeRuleService | None = None,
    feedback_queue: InMemoryFeedbackQueue | None = None,
    message: str = "no, don't book without asking",
) -> tuple[ChatCall, InMemoryFeedbackQueue]:
    """POST one turn; the queue is the observer for what the route enqueued."""
    session_id = uuid4()
    queue = feedback_queue or InMemoryFeedbackQueue()
    client = build_client(
        execution_module or FakeExecutionModule(),
        FakeInteractionService(existing_session=Session(id=session_id)),
        rule_service=rule_service,
        feedback_queue=queue,
    )
    response = client.post(
        "/chat",
        json={"message": message, "session_id": str(session_id)},
        headers=AUTH_HEADERS,
    )
    return ChatCall(response=response, session_id=session_id), queue


def test_one_row_carries_the_turn():
    """The row is written after run_chat returns, naming the rules read before
    it, the assistant text and the user's reply."""
    rule = Rule(condition="the user asks to book", action="ask before booking")

    call, queue = _post(rule_service=FakeRuleService([rule]))

    assert call.response.status_code == 200
    assert call.response.json()["message"] == "of course, booking it"
    assert len(queue.items) == 1
    row = queue.items[0]
    assert row.session_id == call.session_id
    assert row.rule_ids == (rule.id,)
    assert row.assistant_turn == "of course, booking it"
    assert row.user_reply == "no, don't book without asking"


def test_a_promotion_line_is_not_classified_as_the_assistant_turn():
    """The line is fixed copy, not the model's words: the classifier reads the
    assistant turn as the model produced it, yet the user still sees the line."""
    notice = "Rule confirmed: the user asks to book → ask before booking"

    call, queue = _post(
        execution_module=FakeExecutionModule(
            message=f"{notice}\nof course, booking it", notice=notice
        ),
        rule_service=FakeRuleService([Rule(condition="the user asks to book", action="ask first")]),
    )

    assert call.response.status_code == 200
    assert call.response.json()["message"] == f"{notice}\nof course, booking it"
    row = queue.items[0]
    assert row.assistant_turn == "of course, booking it"
    assert "Rule confirmed:" not in row.assistant_turn
    assert row.user_reply == "no, don't book without asking"


def test_the_snapshot_is_read_before_the_turn():
    calls: list[str] = []

    _post(
        execution_module=FakeExecutionModule(calls=calls),
        rule_service=FakeRuleService([Rule(condition="c", action="a")], calls=calls),
    )

    assert calls == ["list_active", "run_chat"]


def test_model_text_strips_the_notice_prefix_and_nothing_else():
    notice = "Rule confirmed: c → a"

    assert _model_text(f"{notice}\nthe answer", notice) == "the answer"
    assert _model_text("the answer", None) == "the answer"
    assert _model_text("the answer", notice) == "the answer"


def test_no_row_when_the_snapshot_was_empty():
    call, queue = _post(rule_service=FakeRuleService([]))

    assert call.response.status_code == 200
    assert queue.items == []


def test_no_row_when_the_turn_failed():
    call, queue = _post(
        execution_module=FakeExecutionModule(exc=RuntimeError("boom")),
        rule_service=FakeRuleService([Rule(condition="c", action="a")]),
    )

    assert call.response.status_code == 500
    assert queue.items == []


def test_the_response_is_unchanged_when_the_enqueue_raises():
    """A storage failure never reaches the user: enqueue_turn swallows it."""
    call, _ = _post(
        rule_service=FakeRuleService([Rule(condition="c", action="a")]),
        feedback_queue=RaisingQueue(),
    )

    assert call.response.status_code == 200
    assert call.response.json()["message"] == "of course, booking it"


def test_the_response_is_unchanged_when_the_snapshot_read_raises():
    """A failing list_active costs attribution, never the turn."""
    call, queue = _post(rule_service=FakeRuleService(exc=RuntimeError("database down")))

    assert call.response.status_code == 200
    assert call.response.json()["message"] == "of course, booking it"
    assert queue.items == []
