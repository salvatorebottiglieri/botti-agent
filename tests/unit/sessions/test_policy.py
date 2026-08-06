"""Tests for sessions.policy free functions."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from cortex.sessions import policy
from cortex.sessions.models import Message, MessageRole, Session, SessionState, SessionWithMessages


@pytest.fixture
def mock_repo():
    repo = MagicMock()
    repo.create = AsyncMock()
    repo.get = AsyncMock()
    repo.update_state = AsyncMock()
    repo.add_message = AsyncMock()
    repo.get_messages = AsyncMock(return_value=[])
    return repo


class TestCreateSession:
    @pytest.mark.asyncio
    async def test_creates_then_marks_active(self, mock_repo):
        session_id = uuid4()
        created = Session(id=session_id, state=SessionState.CREATED)
        active = Session(id=session_id, state=SessionState.ACTIVE)
        mock_repo.create.return_value = created
        mock_repo.update_state.return_value = active

        result = await policy.create_session(mock_repo)

        assert result.state == SessionState.ACTIVE
        mock_repo.create.assert_called_once()
        mock_repo.update_state.assert_called_once_with(session_id, SessionState.ACTIVE)


class TestResumeSession:
    @pytest.mark.asyncio
    async def test_resumes_idle_session(self, mock_repo):
        session_id = uuid4()
        mock_repo.get.return_value = Session(id=session_id, state=SessionState.IDLE)
        mock_repo.update_state.return_value = Session(id=session_id, state=SessionState.ACTIVE)

        result = await policy.resume_session(mock_repo, session_id)

        assert result.state == SessionState.ACTIVE
        mock_repo.update_state.assert_called_once_with(session_id, SessionState.ACTIVE)

    @pytest.mark.asyncio
    async def test_returns_none_for_ended_session(self, mock_repo):
        session_id = uuid4()
        mock_repo.get.return_value = Session(id=session_id, state=SessionState.ENDED)

        result = await policy.resume_session(mock_repo, session_id)

        assert result is None
        mock_repo.update_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_for_missing_session(self, mock_repo):
        mock_repo.get.return_value = None

        result = await policy.resume_session(mock_repo, uuid4())

        assert result is None

    @pytest.mark.asyncio
    async def test_passes_through_active_session_unchanged(self, mock_repo):
        session_id = uuid4()
        active = Session(id=session_id, state=SessionState.ACTIVE)
        mock_repo.get.return_value = active

        result = await policy.resume_session(mock_repo, session_id)

        assert result is active
        mock_repo.update_state.assert_not_called()


class TestAddUserMessage:
    @pytest.mark.asyncio
    async def test_auto_resumes_idle_session(self, mock_repo):
        session_id = uuid4()
        mock_repo.get.return_value = Session(id=session_id, state=SessionState.IDLE)

        await policy.add_user_message(mock_repo, session_id, "hello")

        mock_repo.update_state.assert_called_once_with(session_id, SessionState.ACTIVE)
        mock_repo.add_message.assert_called_once_with(
            session_id=session_id,
            role=MessageRole.USER,
            content="hello",
        )

    @pytest.mark.asyncio
    async def test_does_not_resume_active_session(self, mock_repo):
        session_id = uuid4()
        mock_repo.get.return_value = Session(id=session_id, state=SessionState.ACTIVE)

        await policy.add_user_message(mock_repo, session_id, "hello")

        mock_repo.update_state.assert_not_called()
        mock_repo.add_message.assert_called_once()


class TestGetConversation:
    @pytest.mark.asyncio
    async def test_bundles_session_with_messages(self, mock_repo):
        session_id = uuid4()
        session = Session(id=session_id, state=SessionState.ACTIVE)
        msgs = [Message(session_id=session_id, role=MessageRole.USER, content="hi")]
        mock_repo.get.return_value = session
        mock_repo.get_messages.return_value = msgs

        result = await policy.get_conversation(mock_repo, session_id, limit=10)

        assert isinstance(result, SessionWithMessages)
        assert result.session is session
        assert result.messages == msgs

    @pytest.mark.asyncio
    async def test_returns_none_when_session_missing(self, mock_repo):
        mock_repo.get.return_value = None

        result = await policy.get_conversation(mock_repo, uuid4())

        assert result is None


class TestEndSession:
    @pytest.mark.asyncio
    async def test_marks_ended_with_timestamp(self, mock_repo):
        session_id = uuid4()
        mock_repo.update_state.return_value = Session(id=session_id, state=SessionState.ENDED)

        result = await policy.end_session(mock_repo, session_id)

        assert result.state == SessionState.ENDED
        # First positional arg is session_id; second is state; ended_at is kwarg
        call = mock_repo.update_state.call_args
        assert call.args[0] == session_id
        assert call.args[1] == SessionState.ENDED
        assert call.kwargs["ended_at"] is not None


class TestGetOrCreateSession:
    @pytest.mark.asyncio
    async def test_returns_existing_when_found(self, mock_repo):
        session_id = uuid4()
        existing = Session(id=session_id, state=SessionState.ACTIVE)
        mock_repo.get.return_value = existing

        result = await policy.get_or_create_session(mock_repo, session_id)

        assert result is existing
        mock_repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_new_when_id_is_none(self, mock_repo):
        new_session = Session(id=uuid4(), state=SessionState.CREATED)
        active = Session(id=new_session.id, state=SessionState.ACTIVE)
        mock_repo.create.return_value = new_session
        mock_repo.update_state.return_value = active

        result = await policy.get_or_create_session(mock_repo, None)

        assert result.state == SessionState.ACTIVE
        mock_repo.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_creates_new_when_id_unknown(self, mock_repo):
        mock_repo.get.return_value = None
        new_session = Session(id=uuid4(), state=SessionState.CREATED)
        active = Session(id=new_session.id, state=SessionState.ACTIVE)
        mock_repo.create.return_value = new_session
        mock_repo.update_state.return_value = active

        result = await policy.get_or_create_session(mock_repo, uuid4())

        assert result.state == SessionState.ACTIVE
        mock_repo.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_ended_session_is_terminal_creates_new(self, mock_repo):
        """INVARIANT SP1: an ENDED session is terminal — get_or_create must
        never hand it back as a resumable session."""
        ended = Session(id=uuid4(), state=SessionState.ENDED)
        mock_repo.get.return_value = ended
        new_session = Session(id=uuid4(), state=SessionState.CREATED)
        active = Session(id=new_session.id, state=SessionState.ACTIVE)
        mock_repo.create.return_value = new_session
        mock_repo.update_state.return_value = active

        result = await policy.get_or_create_session(mock_repo, ended.id)

        assert result.state == SessionState.ACTIVE
        mock_repo.create.assert_called_once()
        mock_repo.update_state.assert_called_once_with(new_session.id, SessionState.ACTIVE)
