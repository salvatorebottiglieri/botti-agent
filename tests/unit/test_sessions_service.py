"""Tests for SessionService."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
from datetime import datetime, timezone

from cortex.sessions.service import SessionService
from cortex.sessions.models import Session, SessionState, Message, MessageRole


class TestSessionService:
    """Test cases for SessionService."""

    @pytest.fixture
    def mock_repository(self):
        """Create a mock repository."""
        mock = MagicMock(spec=SessionService)
        mock.create = AsyncMock()
        mock.get = AsyncMock()
        mock.update_state = AsyncMock()
        mock.add_message = AsyncMock()
        mock.get_messages = AsyncMock()
        mock.list_active = AsyncMock()
        return mock

    @pytest.fixture
    def service(self, mock_repository):
        """Create a service with mock repository."""
        return SessionService(mock_repository)

    @pytest.mark.asyncio
    async def test_create_session(self, service, mock_repository):
        """Test creating a new session."""
        session_id = uuid4()
        created_session = Session(id=session_id, state=SessionState.CREATED)
        active_session = Session(id=session_id, state=SessionState.ACTIVE)
        
        mock_repository.create.return_value = created_session
        mock_repository.update_state.return_value = active_session

        result = await service.create_session()

        assert result.state == SessionState.ACTIVE
        mock_repository.create.assert_called_once()
        mock_repository.update_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_session(self, service, mock_repository):
        """Test getting a session."""
        session_id = uuid4()
        session = Session(id=session_id, state=SessionState.ACTIVE)
        mock_repository.get.return_value = session

        result = await service.get_session(session_id)

        assert result is not None
        assert result.id == session_id

    @pytest.mark.asyncio
    async def test_get_session_not_found(self, service, mock_repository):
        """Test getting a non-existent session."""
        mock_repository.get.return_value = None

        result = await service.get_session(uuid4())

        assert result is None

    @pytest.mark.asyncio
    async def test_resume_idle_session(self, service, mock_repository):
        """Test resuming an idle session."""
        session_id = uuid4()
        idle_session = Session(id=session_id, state=SessionState.IDLE)
        active_session = Session(id=session_id, state=SessionState.ACTIVE)
        
        mock_repository.get.return_value = idle_session
        mock_repository.update_state.return_value = active_session

        result = await service.resume_session(session_id)

        assert result is not None
        assert result.state == SessionState.ACTIVE
        mock_repository.update_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_resume_ended_session(self, service, mock_repository):
        """Test resuming an ended session returns None."""
        session_id = uuid4()
        ended_session = Session(id=session_id, state=SessionState.ENDED)
        mock_repository.get.return_value = ended_session

        result = await service.resume_session(session_id)

        assert result is None
        mock_repository.update_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_end_session(self, service, mock_repository):
        """Test ending a session."""
        session_id = uuid4()
        ended_session = Session(id=session_id, state=SessionState.ENDED)
        mock_repository.update_state.return_value = ended_session

        result = await service.end_session(session_id)

        assert result is not None
        assert result.state == SessionState.ENDED

    @pytest.mark.asyncio
    async def test_add_user_message(self, service, mock_repository):
        """Test adding a user message."""
        session_id = uuid4()
        session = Session(id=session_id, state=SessionState.ACTIVE)
        message = Message(
            id=uuid4(),
            session_id=session_id,
            role=MessageRole.USER,
            content="Hello!",
        )
        
        mock_repository.get.return_value = session
        mock_repository.add_message.return_value = message

        result = await service.add_user_message(session_id, "Hello!")

        assert result.content == "Hello!"
        assert result.role == MessageRole.USER

    @pytest.mark.asyncio
    async def test_add_user_message_resumes_idle(self, service, mock_repository):
        """Test adding a message resumes idle session."""
        session_id = uuid4()
        idle_session = Session(id=session_id, state=SessionState.IDLE)
        active_session = Session(id=session_id, state=SessionState.ACTIVE)
        message = Message(
            id=uuid4(),
            session_id=session_id,
            role=MessageRole.USER,
            content="Hello!",
        )
        
        mock_repository.get.return_value = idle_session
        mock_repository.update_state.return_value = active_session
        mock_repository.add_message.return_value = message

        result = await service.add_user_message(session_id, "Hello!")

        assert result.content == "Hello!"
        mock_repository.update_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_assistant_message(self, service, mock_repository):
        """Test adding an assistant message."""
        session_id = uuid4()
        message = Message(
            id=uuid4(),
            session_id=session_id,
            role=MessageRole.ASSISTANT,
            content="Hi there!",
        )
        mock_repository.add_message.return_value = message

        result = await service.add_assistant_message(session_id, "Hi there!")

        assert result.content == "Hi there!"
        assert result.role == MessageRole.ASSISTANT

    @pytest.mark.asyncio
    async def test_add_tool_result(self, service, mock_repository):
        """Test adding a tool result message."""
        session_id = uuid4()
        message = Message(
            id=uuid4(),
            session_id=session_id,
            role=MessageRole.TOOL_RESULT,
            content="Command output: files listed",
        )
        mock_repository.add_message.return_value = message

        result = await service.add_tool_result(session_id, "Command output: files listed")

        assert result.role == MessageRole.TOOL_RESULT

    @pytest.mark.asyncio
    async def test_get_conversation(self, service, mock_repository):
        """Test getting session with messages."""
        session_id = uuid4()
        session = Session(id=session_id, state=SessionState.ACTIVE)
        messages = [
            Message(id=uuid4(), session_id=session_id, role=MessageRole.USER, content="Hi"),
            Message(id=uuid4(), session_id=session_id, role=MessageRole.ASSISTANT, content="Hello!"),
        ]
        
        mock_repository.get.return_value = session
        mock_repository.get_messages.return_value = messages

        result = await service.get_conversation(session_id)

        assert result is not None
        assert result.session.id == session_id
        assert len(result.messages) == 2

    @pytest.mark.asyncio
    async def test_list_active_sessions(self, service, mock_repository):
        """Test listing active sessions."""
        sessions = [
            Session(id=uuid4(), state=SessionState.ACTIVE),
            Session(id=uuid4(), state=SessionState.ACTIVE),
        ]
        mock_repository.list_active.return_value = sessions

        result = await service.list_active_sessions()

        assert len(result) == 2
