"""Chat routes - non-streaming and streaming chat endpoints."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from cortex.agentic.models import Mode as AgentMode
from cortex.api.auth import get_api_key
from cortex.api.dependencies import (
    get_execution_module,
    get_interaction_service,
)
from cortex.api.schemas import ChatRequest, ChatResponse, ErrorResponse

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post(
    "",
    summary="Chat completion (non-streaming)",
    description="Send a message and receive a response from the agent.",
    responses={
        400: {"model": ErrorResponse, "description": "Bad request"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        422: {"model": ErrorResponse, "description": "Validation error"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def chat(
    request: ChatRequest,
    key: str = Depends(get_api_key),
    interaction_service=Depends(get_interaction_service),
    execution_module=Depends(get_execution_module),
) -> ChatResponse:
    """
    Non-streaming chat endpoint.

    Args:
        request: Chat request with message and optional session_id
        key: API key (validated by dependency)
        interaction_service: For session management
        execution_module: For running the agentic loop

    Returns:
        ChatResponse with message, iterations, tools_used
    """
    try:
        # Get or create session
        if request.session_id:
            session = await interaction_service.get_session(request.session_id)
            if not session:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"error": "not_found", "detail": "Session not found"},
                )
            session_id = request.session_id
        else:
            # Create new session
            session = await interaction_service._get_or_create_session(None)
            session_id = session.id

        # Run chat
        mode = AgentMode.GOAL if request.mode == "goal" else AgentMode.CHAT
        max_iterations = request.max_iterations or 20

        response = await execution_module.run_chat(
            session_id=session_id,
            user_message=request.message,
            max_iterations=max_iterations,
        )

        return ChatResponse(
            session_id=session_id,
            message=response.message,
            iterations=response.iterations,
            tools_used=response.tools_used or [],
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "internal_error", "detail": str(e)},
        )


@router.post(
    "/stream",
    summary="Chat completion (streaming SSE)",
    description="Send a message and receive a streaming response via Server-Sent Events.",
)
async def chat_stream(
    request: ChatRequest,
    key: str = Depends(get_api_key),
    interaction_service=Depends(get_interaction_service),
    execution_module=Depends(get_execution_module),
) -> StreamingResponse:
    """
    Streaming chat endpoint using Server-Sent Events.

    Events emitted:
    - thinking: Agent is thinking
    - text: Text delta
    - tool_start: Tool execution starting
    - tool_done: Tool execution completed
    - done: Response complete
    - error: Error occurred
    """

    async def event_generator() -> AsyncGenerator[str, None]:
        session_id: UUID | None = request.session_id

        try:
            # Get or create session
            if session_id:
                session = await interaction_service.get_session(session_id)
                if not session:
                    yield _sse_event("error", {"message": "Session not found"})
                    return
            else:
                session = await interaction_service._get_or_create_session(None)
                session_id = session.id

            # Send initial thinking event
            yield _sse_event("thinking", {"message": "Processing your request..."})

            # Run chat (simplified - full streaming would need loop modification)
            mode = AgentMode.GOAL if request.mode == "goal" else AgentMode.CHAT
            max_iterations = request.max_iterations or 20

            response = await execution_module.run_chat(
                session_id=session_id,
                user_message=request.message,
                max_iterations=max_iterations,
            )

            # Stream the response text
            yield _sse_event("text", {"delta": response.message})
    # Stream the response text
            yield _sse_event(
                "done",
                {
                    "final_message": response.message,
                    "tool_calls": response.tools_used or [],
                    "iterations": response.iterations,
                    "duration_ms": 0,  # TODO: track duration
                },
            )

        except Exception as e:
            yield _sse_event("error", {"message": str(e)})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


def _sse_event(event_type: str, data: dict) -> str:
    """Format data as an SSE event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
