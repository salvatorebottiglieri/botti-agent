"""Session lifecycle policy.

Small free functions that encode session state-machine rules
(auto-ACTIVE on create, auto-resume from IDLE, etc.). Each takes a
SessionRepository — they are pure policy layered on top of persistence.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from cortex.sessions.interfaces import SessionRepository
from cortex.sessions.models import (
    Message,
    MessageRole,
    Session,
    SessionState,
    SessionWithMessages,
)


async def create_session(repo: SessionRepository) -> Session:
    """Create a new session and immediately mark it ACTIVE."""
    session = await repo.create()
    return await repo.update_state(session.id, SessionState.ACTIVE)


async def resume_session(repo: SessionRepository, session_id: UUID) -> Session | None:
    """Resume an IDLE session. Returns None if the session is ENDED or missing."""
    session = await repo.get(session_id)
    if session is None:
        return None
    if session.state == SessionState.IDLE:
        return await repo.update_state(session_id, SessionState.ACTIVE)
    if session.state == SessionState.ENDED:
        return None
    return session


async def add_user_message(
    repo: SessionRepository, session_id: UUID, content: str
) -> Message:
    """Add a user message; auto-resume the session if it was IDLE."""
    session = await repo.get(session_id)
    if session and session.state == SessionState.IDLE:
        await repo.update_state(session_id, SessionState.ACTIVE)
    return await repo.add_message(
        session_id=session_id,
        role=MessageRole.USER,
        content=content,
    )


async def get_conversation(
    repo: SessionRepository, session_id: UUID, limit: int = 50
) -> SessionWithMessages | None:
    """Bundle a session with its recent messages."""
    session = await repo.get(session_id)
    if session is None:
        return None
    messages = await repo.get_messages(session_id, limit=limit)
    return SessionWithMessages(session=session, messages=messages)


async def end_session(repo: SessionRepository, session_id: UUID) -> Session | None:
    """Mark a session ENDED."""
    return await repo.update_state(
        session_id,
        SessionState.ENDED,
        ended_at=datetime.now(timezone.utc),
    )


async def get_or_create_session(
    repo: SessionRepository, session_id: UUID | None
) -> Session:
    """Look up a session by id, or create a new ACTIVE one if id is None or missing."""
    if session_id is not None:
        session = await repo.get(session_id)
        if session is not None:
            return session
    return await create_session(repo)
