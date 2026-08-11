"""Chat routes - non-streaming and streaming chat endpoints."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from cortex.agentic.events import (
    ErrorEvent,
    ResponseDoneEvent,
    TextDeltaEvent,
    ThinkingEvent,
    ToolResultEvent,
    ToolStartEvent,
)
from cortex.api.auth import get_api_key
from cortex.api.dependencies import (
    get_execution_module,
    get_interaction_service,
)
from cortex.api.schemas import ChatRequest, ChatResponse, ErrorResponse

if TYPE_CHECKING:
    from cortex.execution.module import ExecutionModule
    from cortex.interaction.service import InteractionService

logger = logging.getLogger(__name__)

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
    interaction_service: InteractionService = Depends(get_interaction_service),
    execution_module: ExecutionModule = Depends(get_execution_module),
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
    interaction_service: InteractionService = Depends(get_interaction_service),
    execution_module: ExecutionModule = Depends(get_execution_module),
) -> StreamingResponse:
    """
    Streaming chat endpoint using Server-Sent Events.

    Session resolution happens before the stream starts: a provided
    session_id that does not exist is an HTTP 404 (same as the
    non-streaming endpoint); an absent session_id creates a session
    through the same policy. The stream itself only iterates
    ``execution_module.stream_chat()`` and maps each LoopEvent to an
    SSE frame (one vocabulary — the event_type IS the wire name).

    Events emitted:
    - thinking: Agent is thinking
    - text: Text delta
    - tool_start: Tool execution starting
    - tool_done: Tool execution completed
    - done: Response complete
    - error: Error occurred
    """
    # Get or create session (before the stream, same policy as non-streaming)
    if request.session_id:
        session = await interaction_service.get_session(request.session_id)
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "not_found", "detail": "Session not found"},
            )
        session_id = request.session_id
    else:
        session = await interaction_service._get_or_create_session(None)
        session_id = session.id

    max_iterations = request.max_iterations or 20

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            async for event in execution_module.stream_chat(
                session_id=session_id,
                user_message=request.message,
                max_iterations=max_iterations,
            ):
                match event:
                    case ThinkingEvent():
                        yield _sse_event("thinking", {"message": event.message})
                    case TextDeltaEvent():
                        yield _sse_event("text", {"delta": event.delta})
                    case ToolStartEvent():
                        yield _sse_event(
                            "tool_start",
                            {
                                "tool_name": event.tool_name,
                                "tool_call_id": event.tool_call_id,
                            },
                        )
                    case ToolResultEvent():
                        yield _sse_event(
                            "tool_done",
                            {
                                "tool_name": event.tool_name,
                                "tool_call_id": event.tool_call_id,
                                "success": event.success,
                                "output": event.output,
                                "error": event.error,
                                "execution_time_ms": event.execution_time_ms,
                            },
                        )
                    case ResponseDoneEvent():
                        yield _sse_event(
                            "done",
                            {
                                "final_message": event.message,
                                "tool_calls": event.tools_used,
                                "iterations": event.iterations,
                            },
                        )
                    case ErrorEvent():
                        # Terminal frame; the loop's reraise stays silent for
                        # expected conditions (e.g. max_iterations).
                        yield _sse_event("error", {"error": event.error, "code": event.code})
                        return
                    case _:
                        # Never silently drop progress from an unknown event.
                        logger.warning(f"Unhandled loop event: {type(event).__name__}")
        except Exception as exc:
            # Unexpected adapter errors (bugs, serialization) — logged, not silent.
            logger.exception("Unexpected error streaming chat events")
            yield _sse_event("error", {"error": str(exc), "code": None})
            raise

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


def _sse_event(event_type: str, data: dict[str, Any]) -> str:
    """Format data as an SSE event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
