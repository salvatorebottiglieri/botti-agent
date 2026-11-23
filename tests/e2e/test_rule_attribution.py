"""End to end: a correction given in chat moves a Rule (#172).

The whole seam, no network and no database: the real ``chat`` route writes the
real queue row through the app state, the real ``FeedbackDrain`` attributes it
with a scripted provider, and ``RuleService.list_active()`` — what
``cortex rules:list`` prints — reflects the moved confidence.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from cortex.api.auth import get_api_key
from cortex.api.dependencies import get_execution_module, get_interaction_service
from cortex.llm.models import ChatMessage, ChatResult, Role
from cortex.main import create_app
from cortex.rules.attribution import AttributionRun
from cortex.rules.feedback import FeedbackClassifier
from cortex.rules.models import RuleSpec, RuleState, UseOutcome
from cortex.rules.queue import FeedbackDrain, QueuedFeedbackStatus
from cortex.rules.service import RuleService
from cortex.sessions.models import Session
from tests.eval.fakes import ScriptedLLMClient
from tests.unit.rules.fakes import InMemoryFeedbackQueue, InMemoryRuleRepository

AUTH_HEADERS = {"Authorization": "Bearer dummy-key"}
SPEC = RuleSpec(condition="the user asks to book a table", action="ask before booking")
ASSISTANT_TURN = "of course, I booked the table for eight"
CORRECTION = "no, you must ask me before booking a table"


class FakeExecutionModule:
    """The agent loop's answer, scripted — the provider is only the classifier's."""

    async def run_chat(
        self, session_id: UUID, user_message: str, *, max_iterations: int = 20
    ) -> SimpleNamespace:
        return SimpleNamespace(
            message=ASSISTANT_TURN, iterations=1, tools_used=[], notice=None
        )


class FakeInteractionService:
    """Serves one session; the route only needs its id."""

    def __init__(self, session: Session):
        self._session = session

    async def get_session(self, session_id: UUID) -> Session | None:
        return self._session if session_id == self._session.id else None

    async def get_or_create_session(self, session_id: UUID | None) -> Session:
        return self._session


def _client(service: RuleService, queue: InMemoryFeedbackQueue, session_id: UUID) -> TestClient:
    settings = MagicMock()
    settings.version = "0.1.0"
    with patch("cortex.main.get_settings", return_value=settings):
        app = create_app(cortex_state={"rule_service": service, "feedback_queue": queue})
    app.dependency_overrides[get_api_key] = lambda: "dummy-key"
    app.dependency_overrides[get_execution_module] = lambda: FakeExecutionModule()
    app.dependency_overrides[get_interaction_service] = lambda: FakeInteractionService(
        Session(id=session_id)
    )
    return TestClient(app)


async def test_a_correction_in_chat_moves_a_staged_rule() -> None:
    repository = InMemoryRuleRepository()
    service = RuleService(repository)
    rule = await service.create_from_correction(SPEC)
    # An earlier approval leaves the rule staged but confident: the correction
    # on this turn is what moves it back down.
    await service.record_use(
        rule.id, uuid4(), "the user asks to book a table", UseOutcome.APPROVAL
    )
    before = await repository.get(rule.id)
    assert before is not None
    assert before.state is RuleState.STAGING
    assert before.confidence > 0.9

    queue = InMemoryFeedbackQueue()
    provider = ScriptedLLMClient([
        ChatResult(
            message=ChatMessage(
                role=Role.ASSISTANT,
                content=json.dumps(
                    {
                        "uses": [{"rule_id": str(rule.id), "outcome": "correction"}],
                        "proposals": [],
                    }
                ),
            )
        )
    ])
    session_id = uuid4()

    response = _client(service, queue, session_id).post(
        "/chat",
        json={"message": CORRECTION, "session_id": str(session_id)},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert len(queue.items) == 1
    assert queue.items[0].rule_ids == (rule.id,)

    drain = FeedbackDrain(queue, AttributionRun(FeedbackClassifier(provider), service), service)
    assert await drain.drain() == 1

    stored = await repository.get(rule.id)
    assert stored is not None
    assert stored.confidence < before.confidence
    # One correction back to the 0.5 prior: the earlier approval cancels out.
    assert stored.confidence == 0.5
    assert stored.state is RuleState.STAGING
    assert queue.items[0].status is QueuedFeedbackStatus.ATTRIBUTED

    # What `cortex rules:list` prints goes through the same sweep.
    assert [
        (active.id, active.state, active.confidence) for active in await service.list_active()
    ] == [(rule.id, RuleState.STAGING, stored.confidence)]

    # The classifier saw the turn and the rule it was in context with.
    sent = "\n".join(message.content or "" for message in provider.calls[0])
    assert str(rule.id) in sent
    assert CORRECTION in sent
