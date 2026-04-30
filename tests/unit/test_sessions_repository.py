"""Tests for PostgresSessionRepository using mocks."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from datetime import datetime, timezone

from cortex.sessions.repository import PostgresSessionRepository
from cortex.sessions.models import Session, SessionState, Message, MessageRole


class TestPostgresSessionRepository:
    """Test cases for PostgresSessionRepository."""

    @pytest.fixture
    def mock_db_session(self):
        """Create a mock DbSession context manager."""
        mock = MagicMock()
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=None)
        mock.fetchrow = AsyncMock()
        mock.fetch = AsyncMock()
        mock.execute = AsyncMock()
        return mock

    @pytest.fixture
    def repo_with_mock(self, mock_db_session):
        """Create a repository with mocked DB."""
        with patch("cortex.sessions.repository.DbSession") as MockDbSession:
            MockDbSession.return_value = mock_db_session
            yield PostgresSessionRepository(), mock_db_session

    @pytest.mark.asyncio
    async def test_create_session(self, repo_with_mock):
        """Test creating a new session."""
        repo, mock_db_session = repo_with_mock
        session_id = uuid4()
        now = datetime.now(timezone.utc)
        mock_db_session.fetchrow.return_value = {
            "id": session_id,
            "state": "created",
            "created_at": now,
            "last_activity_at": now,
            "ended_at": None,
            "metadata": {},
        }

        result = await repo.create()

        assert result.id == session_id
        assert result.state == SessionState.CREATED
        mock_db_session.fetchrow.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_session_found(self, repo_with_mock):
        """Test getting an existing session."""
        repo, mock_db_session = repo_with_mock
        session_id = uuid4()
        now = datetime.now(timezone.utc)
        mock_db_session.fetchrow.return_value = {
            "id": session_id,
            "state": "active",
            "created_at": now,
            "last_activity_at": now,
            "ended_at": None,
            "metadata": {"source": "api"},
        }

        result = await repo.get(session_id)

        assert result is not None
        assert result.id == session_id
        assert result.state == SessionState.ACTIVE

    @pytest.mark.asyncio
    async def test_get_session_not_found(self, repo_with_mock):
        """Test getting a non-existent session."""
        repo, mock_db_session = repo_with_mock
        mock_db_session.fetchrow.return_value = None

        result = await repo.get(uuid4())

        assert result is None

    @pytest.mark.asyncio
    async def test_update_state(self, repo_with_mock):
        """Test updating session state."""
        repo, mock_db_session = repo_with_mock
        session_id = uuid4()
        now = datetime.now(timezone.utc)
        mock_db_session.fetchrow.return_value = {
            "id": session_id,
            "state": "ended",
            "created_at": now,
            "last_activity_at": now,
            "ended_at": now,
            "metadata": {},
        }

        result = await repo.update_state(session_id, SessionState.ENDED, now)

        assert result is not None
        assert result.state == SessionState.ENDED
        assert result.ended_at is not None

    @pytest.mark.asyncio
    async def test_add_message(self, repo_with_mock):
        """Test adding a message to a session."""
        repo, mock_db_session = repo_with_mock
        session_id = uuid4()
        message_id = uuid4()
        now = datetime.now(timezone.utc)
        mock_db_session.fetchrow.return_value = {
            "id": message_id,
            "session_id": session_id,
            "role": "user",
            "content": "Hello!",
            "tool_calls": None,
            "created_at": now,
        }

        result = await repo.add_message(
            session_id=session_id,
            role=MessageRole.USER,
            content="Hello!",
        )

        assert result.id == message_id
        assert result.content == "Hello!"
        assert result.role == MessageRole.USER

    @pytest.mark.asyncio
    async def test_add_message_with_tool_calls(self, repo_with_mock):
        """Test adding a message with tool calls."""
        repo, mock_db_session = repo_with_mock
        session_id = uuid4()
        message_id = uuid4()
        now = datetime.now(timezone.utc)
        tool_calls = [{"name": "shell", "arguments": {"command": "ls"}}]
        mock_db_session.fetchrow.return_value = {
            "id": message_id,
            "session_id": session_id,
            "role": "assistant",
            "content": "Running command...",
            "tool_calls": tool_calls,
            "created_at": now,
        }

        result = await repo.add_message(
            session_id=session_id,
            role=MessageRole.ASSISTANT,
            content="Running command...",
            tool_calls=tool_calls,
        )

        assert result.tool_calls == tool_calls

    @pytest.mark.asyncio
    async def test_get_messages(self, repo_with_mock):
        """Test getting messages for a session."""
        repo, mock_db_session = repo_with_mock
        session_id = uuid4()
        now = datetime.now(timezone.utc)
        mock_db_session.fetch.return_value = [
            {
                "id": uuid4(),
                "session_id": session_id,
                "role": "user",
                "content": "Hello!",
                "tool_calls": None,
                "created_at": now,
            },
            {
                "id": uuid4(),
                "session_id": session_id,
                "role": "assistant",
                "content": "Hi there!",
                "tool_calls": None,
                "created_at": now,
            },
        ]

        result = await repo.get_messages(session_id)

        assert len(result) == 2
        # DB returns newest first (ORDER BY created_at DESC), reversed back to oldest→newest
        # Mock returns [Hello, Hi there], reversed = [Hi there, Hello] which is newest first
        assert result[0].content == "Hi there!"  # newest
        assert result[1].content == "Hello!"  # oldest

    @pytest.mark.asyncio
    async def test_list_active_sessions(self, repo_with_mock):
        """Test listing active sessions."""
        repo, mock_db_session = repo_with_mock
        now = datetime.now(timezone.utc)
        mock_db_session.fetch.return_value = [
            {
                "id": uuid4(),
                "state": "active",
                "created_at": now,
                "last_activity_at": now,
                "ended_at": None,
                "metadata": {},
            },
        ]

        result = await repo.list_active()

        assert len(result) == 1
        assert result[0].state == SessionState.ACTIVE
