"""Tests for session models."""

import pytest
from uuid import uuid4

from cortex.sessions.models import (
    Session,
    SessionState,
    Message,
    MessageRole,
    SessionWithMessages,
)


class TestSession:
    """Test cases for Session model."""

    def test_create_session_with_defaults(self):
        """Test creating a session with default values."""
        session = Session()
        
        assert session.id is not None
        assert session.state == SessionState.CREATED
        assert session.ended_at is None
        assert session.metadata == {}

    def test_create_session_with_custom_values(self):
        """Test creating a session with custom values."""
        session_id = uuid4()
        session = Session(
            id=session_id,
            state=SessionState.ACTIVE,
            metadata={"source": "api"},
        )
        
        assert session.id == session_id
        assert session.state == SessionState.ACTIVE
        assert session.metadata == {"source": "api"}

    def test_session_state_enum_values(self):
        """Test session state enum string values."""
        assert SessionState.CREATED.value == "created"
        assert SessionState.ACTIVE.value == "active"
        assert SessionState.IDLE.value == "idle"
        assert SessionState.ENDED.value == "ended"


class TestMessage:
    """Test cases for Message model."""

    def test_create_message_with_defaults(self):
        """Test creating a message with default values."""
        session_id = uuid4()
        message = Message(
            session_id=session_id,
            role=MessageRole.USER,
            content="Hello!",
        )
        
        assert message.id is not None
        assert message.session_id == session_id
        assert message.role == "user"  # enum value
        assert message.content == "Hello!"
        assert message.tool_calls is None

    def test_message_with_tool_calls(self):
        """Test message with serialized tool calls."""
        session_id = uuid4()
        tool_calls = [{"name": "shell", "arguments": {"command": "ls"}}]
        message = Message(
            session_id=session_id,
            role=MessageRole.ASSISTANT,
            content="Running command...",
            tool_calls=tool_calls,
        )
        
        assert message.tool_calls == tool_calls

    def test_message_role_enum_values(self):
        """Test message role enum string values."""
        assert MessageRole.SYSTEM.value == "system"
        assert MessageRole.USER.value == "user"
        assert MessageRole.ASSISTANT.value == "assistant"
        assert MessageRole.TOOL_RESULT.value == "tool_result"


class TestSessionWithMessages:
    """Test cases for SessionWithMessages model."""

    def test_session_with_messages(self):
        """Test combining session with messages."""
        session = Session()
        messages = [
            Message(session_id=session.id, role=MessageRole.USER, content="Hi"),
            Message(session_id=session.id, role=MessageRole.ASSISTANT, content="Hello!"),
        ]
        
        combined = SessionWithMessages(session=session, messages=messages)
        
        assert combined.session == session
        assert len(combined.messages) == 2
        assert combined.messages[0].content == "Hi"
