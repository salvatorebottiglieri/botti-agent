"""The chat route's seams, faked once for both endpoints' tests.

The app is the real ``create_app()`` with the four chat dependencies overridden,
so the tests drive the full HTTP path; only the execution module, the
interaction service, the rule service and the queue are doubles.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID

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
from cortex.sessions.models import Session, SessionState
from tests.unit.rules.fakes import InMemoryFeedbackQueue


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
        self.calls = calls if calls is not None else []

    async def list_active(self) -> list[Rule]:
        self.calls.append("list_active")
        if self._exc is not None:
            raise self._exc
        return list(self._rules)


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
    execution_module: Any,
    interaction_service: FakeInteractionService,
    *,
    rule_service: FakeRuleService | None = None,
    feedback_queue: InMemoryFeedbackQueue | None = None,
    raise_server_exceptions: bool = True,
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
    app.dependency_overrides[get_feedback_queue] = (
        lambda: feedback_queue or InMemoryFeedbackQueue()
    )
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)
