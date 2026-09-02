"""Route-level tests for /sessions.

The seam is the full HTTP path: auth, session state policy, and the 409 on
ended sessions; plus the trace_enabled create/read surface (issue #111 T1).
The repository is a fake so no DB is touched.
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


def _create_session_repo() -> MagicMock:
    """Fake repo where create persists trace_enabled like the Postgres impl.

    policy.create_session calls create(trace_enabled=...) then
    update_state(id, ACTIVE); the ACTIVE session carries the same flag.
    """
    repo = MagicMock()
    created: dict[str, object] = {}

    async def do_create(trace_enabled: bool = False) -> Session:
        created["session"] = Session(state=SessionState.CREATED, trace_enabled=trace_enabled)
        return created["session"]

    async def do_update_state(
        session_id, state: SessionState, ended_at=None
    ) -> Session:
        return created["session"].model_copy(update={"state": state})

    repo.create = AsyncMock(side_effect=do_create)
    repo.update_state = AsyncMock(side_effect=do_update_state)
    return repo


class TestSessionCreateTraceFlag:
    """POST /sessions accepts trace_enabled; default false (issue #111 T1)."""

    def test_create_traced_session_persists_and_returns_flag(self):
        """Creating a traced session stores trace_enabled=true and returns it."""
        repo = _create_session_repo()
        client = build_client(repo)

        response = client.post(
            "/sessions",
            headers=AUTH_HEADERS,
            json={"trace_enabled": True},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["trace_enabled"] is True
        repo.create.assert_called_once_with(trace_enabled=True)

    def test_create_session_defaults_trace_enabled_false(self):
        """A bare POST (no body) creates an untraced session — frontend-compatible."""
        repo = _create_session_repo()
        client = build_client(repo)

        response = client.post("/sessions", headers=AUTH_HEADERS)

        assert response.status_code == 200, response.text
        assert response.json()["trace_enabled"] is False
        repo.create.assert_called_once_with(trace_enabled=False)


class TestSessionGetTraceFlag:
    """GET /sessions/{id} round-trips the flag (issue #111 T1)."""

    def test_get_traced_session_returns_flag_true(self):
        """A traced session reads back with trace_enabled=true."""
        repo = MagicMock()
        repo.get = AsyncMock(
            return_value=Session(
                id=uuid4(),
                state=SessionState.ACTIVE,
                trace_enabled=True,
            )
        )
        repo.get_messages = AsyncMock(return_value=[])
        client = build_client(repo)

        response = client.get(f"/sessions/{repo.get.return_value.id}", headers=AUTH_HEADERS)

        assert response.status_code == 200, response.text
        assert response.json()["session"]["trace_enabled"] is True

    def test_get_session_created_before_flag_returns_false(self):
        """Sessions created before the flag read back as trace_enabled=false."""
        repo = MagicMock()
        repo.get = AsyncMock(
            return_value=Session(
                id=uuid4(),
                state=SessionState.ACTIVE,
            )
        )
        repo.get_messages = AsyncMock(return_value=[])
        client = build_client(repo)

        response = client.get(f"/sessions/{repo.get.return_value.id}", headers=AUTH_HEADERS)

        assert response.status_code == 200, response.text
        assert response.json()["session"]["trace_enabled"] is False
