"""Route-level tests for attribution on POST /chat (#172).

The non-streaming twin of ``test_chat_stream.py``'s attribution class: the
turn's rules are read before the turn and one pending row is written after the
answer, and neither read nor write can refuse the response.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from cortex.api.auth import get_api_key
from cortex.api.dependencies import (
    get_execution_module,
    get_feedback_queue,
    get_interaction_service,
    get_rule_service,
)
from cortex.main import create_app
from cortex.rules.models import Rule
from cortex.sessions.models import Session
from tests.unit.rules.fakes import InMemoryFeedbackQueue

AUTH_HEADERS = {"Authorization": "Bearer dummy-key"}


class FakeExecutionModule:
    """A one-turn run_chat: returns a scripted answer, or raises."""

    def __init__(
        self,
        message: str = "of course, booking it",
        exc: Exception | None = None,
        calls: list[str] | None = None,
    ):
        self._message = message
        self._exc = exc
        self._calls = calls

    async def run_chat(
        self, session_id: UUID, user_message: str, *, max_iterations: int = 20
    ) -> SimpleNamespace:
        if self._calls is not None:
            self._calls.append("run_chat")
        if self._exc is not None:
            raise self._exc
        return SimpleNamespace(message=self._message, iterations=1, tools_used=[])


class FakeRuleService:
    """Serves a scripted active set, or raises, to the attribution seam."""

    def __init__(
        self,
        rules: list[Rule] | None = None,
        exc: Exception | None = None,
        calls: list[str] | None = None,
    ):
        self._rules = rules or []
        self._exc = exc
        self._calls = calls

    async def list_active(self) -> list[Rule]:
        if self._calls is not None:
            self._calls.append("list_active")
        if self._exc is not None:
            raise self._exc
        return list(self._rules)


class RaisingQueue(InMemoryFeedbackQueue):
    """A queue whose insert fails the way a dropped connection does."""

    async def enqueue(self, item):
        raise RuntimeError("connection lost")


class FakeInteractionService:
    """Serves a scripted existing session; the route only needs its id."""

    def __init__(self, existing_session: Session | None = None):
        self._existing_session = existing_session

    async def get_session(self, session_id: UUID) -> Session | None:
        if self._existing_session is not None and self._existing_session.id == session_id:
            return self._existing_session
        return None

    async def get_or_create_session(self, session_id: UUID | None) -> Session:
        if session_id is not None:
            existing = await self.get_session(session_id)
            if existing is not None:
                return existing
        return Session()


def build_client(
    execution_module: FakeExecutionModule,
    interaction_service: FakeInteractionService,
    *,
    rule_service: FakeRuleService | None = None,
    feedback_queue: InMemoryFeedbackQueue | None = None,
) -> TestClient:
    """App with the chat dependencies overridden by fakes."""
    settings = MagicMock()
    settings.version = "0.1.0"
    with patch("cortex.main.get_settings", return_value=settings):
        app = create_app()
    app.dependency_overrides[get_api_key] = lambda: "dummy-key"
    app.dependency_overrides[get_execution_module] = lambda: execution_module
    app.dependency_overrides[get_interaction_service] = lambda: interaction_service
    app.dependency_overrides[get_rule_service] = lambda: rule_service or FakeRuleService()
    app.dependency_overrides[get_feedback_queue] = lambda: feedback_queue or InMemoryFeedbackQueue()
    return TestClient(app)


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


def test_the_snapshot_is_read_before_the_turn():
    calls: list[str] = []

    _post(
        execution_module=FakeExecutionModule(calls=calls),
        rule_service=FakeRuleService([Rule(condition="c", action="a")], calls=calls),
    )

    assert calls == ["list_active", "run_chat"]


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
