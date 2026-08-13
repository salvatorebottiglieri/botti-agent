"""Route-level tests for /sessions message creation (issue: ENDED sessions).

The seam is the full HTTP path: auth, session state policy, and the 409 on
ended sessions. The repository is a fake so no DB is touched.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from cortex.api.auth import get_api_key
from cortex.api.dependencies import get_session_repository
from cortex.main import create_app
from cortex.sessions.models import Session, SessionState

AUTH_HEADERS = {"Authorization": "Bearer dummy-key"}


def build_client(repo) -> TestClient:
    """App with session-repository and auth dependencies overridden."""
    settings = MagicMock()
    settings.version = "0.1.0"
    with patch("cortex.main.get_settings", return_value=settings):
        app = create_app()
    app.dependency_overrides[get_api_key] = lambda: "dummy-key"
    app.dependency_overrides[get_session_repository] = lambda: repo
    return TestClient(app)


def _fake_repo(state: SessionState) -> MagicMock:
    session = Session(
        id=uuid4(),
        state=state,
        created_at=datetime.now(UTC),
        last_activity_at=datetime.now(UTC),
    )
    repo = MagicMock()
    repo.get = AsyncMock(return_value=session)
    return repo


class TestCreateMessageOnEndedSession:
    """POST /sessions/{id}/messages must reject ENDED sessions (SP1)."""

    def test_message_on_ended_session_returns_409(self):
        """A message targeting an ENDED session is rejected with 409 and a
        session_ended error, for every role."""
        repo = _fake_repo(SessionState.ENDED)
        client = build_client(repo)
        session_id = repo.get.return_value.id

        for role in ("user", "assistant", "tool_result"):
            response = client.post(
                f"/sessions/{session_id}/messages",
                headers=AUTH_HEADERS,
                json={"role": role, "content": "should be rejected"},
            )

            assert response.status_code == 409, f"role={role}: {response.text}"
            assert response.json()["detail"]["error"] == "session_ended"

    def test_message_on_active_session_is_accepted(self):
        """The 409 must not break the normal active-session path."""
        repo = _fake_repo(SessionState.ACTIVE)
        repo.add_message = AsyncMock(
            return_value=MagicMock(
                id=uuid4(),
                role="user",
                content="hi",
                tool_calls=None,
                created_at=datetime.now(UTC),
            )
        )
        client = build_client(repo)
        session_id = repo.get.return_value.id

        response = client.post(
            f"/sessions/{session_id}/messages",
            headers=AUTH_HEADERS,
            json={"role": "user", "content": "hi"},
        )

        assert response.status_code == 200
