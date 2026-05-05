"""Session routes - session and message management."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.params import Path

from cortex.api.auth import get_api_key
from cortex.api.dependencies import get_session_service
from cortex.api.schemas import MessageCreate, MessageResponse, SessionResponse, SessionWithMessages

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get(
    "",
    summary="List active sessions",
    description="Returns active sessions, newest first.",
)
async def list_sessions(
    key: str = Depends(get_api_key),
    session_service=Depends(get_session_service),
    limit: int = 10,
) -> list[SessionResponse]:
    """List active sessions."""
    sessions = await session_service.list_active_sessions(limit=limit)
    return [
        SessionResponse(
            id=s.id,
            state=s.state.value,
            created_at=s.created_at,
            last_activity_at=s.last_activity_at,
            ended_at=s.ended_at,
            metadata=s.metadata or {},
        )
        for s in sessions
    ]


@router.post(
    "",
    summary="Create a new session",
    description="Creates a new session and returns it.",
)
async def create_session(
    key: str = Depends(get_api_key),
    session_service=Depends(get_session_service),
) -> SessionResponse:
    """Create a new session."""
    session = await session_service.create_session()
    return SessionResponse(
        id=session.id,
        state=session.state.value,
        created_at=session.created_at,
        last_activity_at=session.last_activity_at,
        ended_at=session.ended_at,
        metadata=session.metadata or {},
    )


@router.get(
    "/{session_id}",
    summary="Get session with messages",
    description="Returns a session with its full conversation history.",
)
async def get_session(
    session_id: Annotated[UUID, Path(description="Session ID")],
    key: str = Depends(get_api_key),
    session_service=Depends(get_session_service),
    limit: int = 50,
) -> SessionWithMessages:
    """Get a session with its messages."""
    result = await session_service.get_conversation(session_id, limit=limit)

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "detail": "Session not found"},
        )

    return SessionWithMessages(
        session=SessionResponse(
            id=result.session.id,
            state=result.session.state.value,
            created_at=result.session.created_at,
            last_activity_at=result.session.last_activity_at,
            ended_at=result.session.ended_at,
            metadata=result.session.metadata or {},
        ),
        messages=[
            MessageResponse(
                id=m.id,
                role=m.role.value,
                content=m.content,
                tool_calls=m.tool_calls,
                created_at=m.created_at,
            )
            for m in result.messages
        ],
    )


@router.post(
    "/{session_id}/messages",
    summary="Add a message to a session",
    description="Adds a message to the conversation history.",
)
async def create_message(
    session_id: Annotated[UUID, Path(description="Session ID")],
    message: MessageCreate,
    key: str = Depends(get_api_key),
    session_service=Depends(get_session_service),
) -> MessageResponse:
    """Add a message to a session."""
    # Verify session exists
    session = await session_service.get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "detail": "Session not found"},
        )

    # Add message based on role
    if message.role == "user":
        msg = await session_service.add_user_message(session_id, message.content)
    elif message.role == "assistant":
        msg = await session_service.add_assistant_message(
            session_id, message.content, message.tool_calls
        )
    else:  # tool_result
        msg = await session_service.add_tool_result(
            session_id, message.content, message.tool_calls
        )

    return MessageResponse(
        id=msg.id,
        role=msg.role.value,
        content=msg.content,
        tool_calls=msg.tool_calls,
        created_at=msg.created_at,
    )


@router.post(
    "/{session_id}/end",
    summary="End a session",
    description="Marks a session as ended.",
)
async def end_session(
    session_id: Annotated[UUID, Path(description="Session ID")],
    key: str = Depends(get_api_key),
    session_service=Depends(get_session_service),
) -> SessionResponse:
    """End a session."""
    session = await session_service.end_session(session_id)

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "detail": "Session not found"},
        )

    return SessionResponse(
        id=session.id,
        state=session.state.value,
        created_at=session.created_at,
        last_activity_at=session.last_activity_at,
        ended_at=session.ended_at,
        metadata=session.metadata or {},
    )


@router.post(
    "/{session_id}/resume",
    summary="Resume an idle session",
    description="Resumes an idle session, making it active again.",
)
async def resume_session(
    session_id: Annotated[UUID, Path(description="Session ID")],
    key: str = Depends(get_api_key),
    session_service=Depends(get_session_service),
) -> SessionResponse:
    """Resume an idle session."""
    session = await session_service.resume_session(session_id)

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "detail": "Session not found or already ended"},
        )

    return SessionResponse(
        id=session.id,
        state=session.state.value,
        created_at=session.created_at,
        last_activity_at=session.last_activity_at,
        ended_at=session.ended_at,
        metadata=session.metadata or {},
    )
