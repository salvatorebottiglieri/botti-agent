"""AgentLoop - Core agentic loop: Think → Act → Observe → Respond."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from cortex.agentic.events import (
    ErrorEvent,
    LoopEvent,
    ResponseDoneEvent,
    TextDeltaEvent,
    ThinkingEvent,
    ToolResultEvent,
    ToolStartEvent,
)
from cortex.agentic.models import (
    ChatResponse,
    DecisionType,
    GoalResult,
    MaxIterationsError,
    Mode,
)
from cortex.events import EventEmitter
from cortex.sessions.models import Message, MessageRole, SessionState

if TYPE_CHECKING:
    from cortex.agentic.context_builder import ContextBuilder
    from cortex.agentic.executor import LoopExecutor
    from cortex.agentic.reasoner import Reasoner
    from cortex.events import EventBus
    from cortex.sessions.interfaces import SessionRepository

logger = logging.getLogger(__name__)


class AgentLoop:
    """
    Core agentic loop: Think → Act → Observe → Respond

    Two modes:
    - CHAT: Interactive conversation
    - GOAL: Background task execution

    The loop:
    1. Context: Build context from session, memory, tools
    2. Think: Ask LLM to reason and decide
    3. Act: Execute tools if needed
    4. Observe: Collect results
    5. Respond: Return to user (or continue loop)
    """

    def __init__(
        self,
        context_builder: ContextBuilder,
        reasoner: Reasoner,
        executor: LoopExecutor,
        event_bus: EventBus | None = None,
        session_repository: SessionRepository | None = None,
        max_chat_iterations: int = 20,
        max_goal_iterations: int = 100,
    ):
        self._context_builder = context_builder
        self._reasoner = reasoner
        self._executor = executor
        self._emitter = EventEmitter(event_bus, source_module="agent_loop")
        self._session_repository = session_repository
        self._max_chat_iterations = max_chat_iterations
        self._max_goal_iterations = max_goal_iterations

    async def run_chat(
        self,
        session_id: UUID,
        user_message: str,
        *,
        max_iterations: int | None = None,
    ) -> ChatResponse:
        """
        Run loop for chat mode.

        Drain wrapper over stream_chat(): consumes the generator, accumulates
        the response text from TextDeltaEvent deltas and the final metadata
        from ResponseDoneEvent, and returns the resulting ChatResponse.
        Progress events (thinking, tool start/result) and ErrorEvent are
        ignored — the generator re-raises the original exception, so errors
        propagate unchanged.

        Args:
            session_id: Current session
            user_message: The user's message
            max_iterations: Optional override for max iterations

        Returns:
            ChatResponse with message and metadata

        Raises:
            MaxIterationsError: If loop exceeds max iterations
        """
        response_text = ""
        tools_used: list[str] = []
        iterations = 0
        async for event in self.stream_chat(
            session_id=session_id,
            user_message=user_message,
            max_iterations=max_iterations,
        ):
            match event:
                case TextDeltaEvent(delta=delta):
                    response_text += delta
                case ResponseDoneEvent(tools_used=used, iterations=iters):
                    tools_used, iterations = used, iters
                # Ignore progress events; stream_chat re-raises the original exception
                case _:
                    pass
        return ChatResponse(
            message=response_text,
            tools_used=tools_used,
            iterations=iterations,
            session_id=session_id,
        )

    async def stream_chat(
        self,
        session_id: UUID,
        user_message: str,
        *,
        max_iterations: int | None = None,
    ) -> AsyncGenerator[LoopEvent, None]:
        """
        Run loop for chat mode, streaming progress events.

        Yields:
            LoopEvent progress signals as the loop runs: a ThinkingEvent per
            iteration, ToolStartEvent/ToolResultEvent per tool call, a
            TextDeltaEvent with the response text, then ResponseDoneEvent.

        Errors:
            On any failure an ErrorEvent is yielded first — code
            "max_iterations" for MaxIterationsError, else None — and then the
            original exception propagates out of the generator.

        Raises:
            MaxIterationsError: If loop exceeds max iterations
        """
        max_iters = max_iterations or self._max_chat_iterations
        iterations = 0
        tools_used: list[str] = []
        messages: list[Message] = []

        # Seed history from the repository so multi-turn context works. The
        # limit mirrors the context builder's window (max_messages - 1) so
        # the seeded conversation never exceeds what the builder expects.
        if self._session_repository is not None:
            history = await self._session_repository.get_messages(
                session_id, limit=self._context_builder.max_messages - 1
            )
            messages.extend(history)

        # Add user message
        messages.append(Message(
            session_id=session_id,
            role=MessageRole.USER,
            content=user_message,
        ))

        # Persist the user message when a repository is wired
        if self._session_repository is not None:
            await self._session_repository.add_message(
                session_id, MessageRole.USER, user_message
            )

        try:
            while iterations < max_iters:
                # 1. Context - build reasoning context
                context = await self._context_builder.build(
                    session_id=session_id,
                    user_message=user_message,
                    mode=Mode.CHAT,
                )

                # Inject current messages into context
                context.conversation = messages

                # 2. Think - reason about what to do
                decision = await self._reasoner.reason(context)

                # Emit the reasoning step for this iteration
                yield ThinkingEvent(session_id, message=decision.reasoning)

                # Handle decision
                match decision.decision_type:
                    case DecisionType.RESPOND:
                        # Done! Stream the response
                        text = decision.text or "I'm not sure how to respond."
                        if self._session_repository is not None:
                            await self._session_repository.add_message(
                                session_id, MessageRole.ASSISTANT, text
                            )
                        yield TextDeltaEvent(session_id, delta=text)
                        yield ResponseDoneEvent(
                            session_id,
                            message=text,
                            tools_used=tools_used,
                            iterations=iterations,
                        )
                        return

                    case DecisionType.ASK_QUESTION:
                        # Return the question
                        text = decision.text or "Could you clarify?"
                        if self._session_repository is not None:
                            await self._session_repository.add_message(
                                session_id, MessageRole.ASSISTANT, text
                            )
                        yield TextDeltaEvent(session_id, delta=text)
                        yield ResponseDoneEvent(
                            session_id,
                            message=text,
                            tools_used=tools_used,
                            iterations=iterations,
                        )
                        return

                    case DecisionType.EXECUTE_TOOLS:
                        # 3. Act - execute tools
                        if decision.tool_calls:
                            # Record the assistant's tool-call decision in the
                            # conversation (internal shape — the LLM provider
                            # translates to its own wire format). Providers
                            # require an assistant message with the tool calls
                            # to precede the tool messages that answer them.
                            messages.append(Message(
                                session_id=session_id,
                                role=MessageRole.ASSISTANT,
                                content="",
                                tool_calls=[
                                    {
                                        "id": call.id,
                                        "name": call.name,
                                        "arguments": call.arguments,
                                    }
                                    for call in decision.tool_calls
                                ],
                            ))

                            # Persist the assistant tool-call message before the
                            # tool messages that answer them (same shape as run_goal).
                            if self._session_repository is not None:
                                await self._session_repository.add_message(
                                    session_id,
                                    MessageRole.ASSISTANT,
                                    "",
                                    tool_calls=[
                                        {
                                            "id": call.id,
                                            "name": call.name,
                                            "arguments": call.arguments,
                                        }
                                        for call in decision.tool_calls
                                    ],
                                )

                            for call in decision.tool_calls:
                                # Track tools used
                                tools_used.append(call.name)

                                yield ToolStartEvent(
                                    session_id,
                                    tool_name=call.name,
                                    tool_call_id=call.id,
                                )

                                result = await self._executor.execute_single(call)

                                yield ToolResultEvent(
                                    session_id,
                                    tool_name=result.tool_name,
                                    tool_call_id=result.tool_call_id,
                                    success=result.success,
                                    output=result.output,
                                    error=result.error,
                                    execution_time_ms=result.execution_time_ms,
                                )

                                # Persist the tool result, linked to its call,
                                # so the next context build sees it.
                                if self._session_repository is not None:
                                    await self._session_repository.add_message(
                                        session_id,
                                        MessageRole.TOOL_RESULT,
                                        (
                                            result.output or ""
                                            if result.success
                                            else f"Error: {result.error}"
                                        ),
                                        tool_call_id=call.id,
                                    )

                                # 4. Observe - add tool result to conversation
                                msg = Message(
                                    session_id=session_id,
                                    role=MessageRole.TOOL_RESULT,
                                    content=(
                                        result.output or ""
                                        if result.success
                                        else f"Error: {result.error}"
                                    ),
                                    tool_call_id=call.id,
                                )
                                messages.append(msg)

                            iterations += 1
                        else:
                            # No tools to execute, respond
                            fallback = "I couldn't determine what tools to use."
                            if self._session_repository is not None:
                                await self._session_repository.add_message(
                                    session_id, MessageRole.ASSISTANT, fallback
                                )
                            yield TextDeltaEvent(session_id, delta=fallback)
                            yield ResponseDoneEvent(
                                session_id,
                                message=fallback,
                                tools_used=tools_used,
                                iterations=iterations,
                            )
                            return

            # Exceeded max iterations
            raise MaxIterationsError(max_iters)
        except Exception as exc:
            yield ErrorEvent(
                session_id,
                error=str(exc),
                code="max_iterations" if isinstance(exc, MaxIterationsError) else None,
            )
            raise exc

    async def run_goal(
        self,
        goal_id: UUID,
        description: str,
        *,
        max_iterations: int | None = None,
    ) -> GoalResult:
        """
        Run loop for goal mode.

        Longer-running, emits goal.status events.
        Max 100 iterations by default.

        Args:
            goal_id: Unique goal identifier
            description: Goal description
            max_iterations: Optional override

        Returns:
            GoalResult with completion status

        Raises:
            MaxIterationsError: If loop exceeds max iterations
        """
        max_iters = max_iterations or self._max_goal_iterations
        iterations = 0
        steps_completed: list[str] = []
        session_id = uuid4()  # Create session for goal

        # Persist a real session when a repository is wired, so tool
        # results feed back into context across iterations (mirrors chat).
        if self._session_repository is not None:
            session = await self._session_repository.create()
            await self._session_repository.update_state(session.id, SessionState.ACTIVE)
            session_id = session.id

        # Emit start event
        await self._emit_goal_event(goal_id, "started", {
            "description": description,
            "timestamp": time.time(),
        })

        while iterations < max_iters:
            # 1. Context
            context = await self._context_builder.build(
                session_id=session_id,
                user_message=description,
                mode=Mode.GOAL,
                goal_id=goal_id,
            )

            # 2. Think
            decision = await self._reasoner.reason(context)

            # Handle decision
            match decision.decision_type:
                case DecisionType.RESPOND:
                    # Goal complete
                    await self._emit_goal_event(goal_id, "completed", {
                        "message": decision.text,
                        "iterations": iterations,
                        "timestamp": time.time(),
                    })

                    return GoalResult(
                        goal_id=goal_id,
                        success=True,
                        message=decision.text or "Goal completed",
                        iterations=iterations,
                        steps_completed=steps_completed,
                    )

                case DecisionType.ASK_QUESTION:
                    # Need clarification for goal
                    await self._emit_goal_event(goal_id, "needs_input", {
                        "question": decision.text,
                        "timestamp": time.time(),
                    })

                    return GoalResult(
                        goal_id=goal_id,
                        success=False,
                        message=decision.text or "Need more information",
                        iterations=iterations,
                        steps_completed=steps_completed,
                        error="Needs clarification",
                    )

                case DecisionType.EXECUTE_TOOLS:
                    # Execute and track
                    if decision.tool_calls:
                        # Persist the assistant's tool-call decision before the
                        # tool messages that answer them (same shape as chat).
                        if self._session_repository is not None:
                            await self._session_repository.add_message(
                                session_id,
                                MessageRole.ASSISTANT,
                                "",
                                tool_calls=[
                                    {
                                        "id": call.id,
                                        "name": call.name,
                                        "arguments": call.arguments,
                                    }
                                    for call in decision.tool_calls
                                ],
                            )

                        results = await self._executor.execute_tools(decision.tool_calls)

                        # Persist tool results, linked to their calls, so the
                        # next context build sees them.
                        if self._session_repository is not None:
                            for call, result in zip(decision.tool_calls, results):
                                await self._session_repository.add_message(
                                    session_id,
                                    MessageRole.TOOL_RESULT,
                                    (
                                        result.output or ""
                                        if result.success
                                        else f"Error: {result.error}"
                                    ),
                                    tool_call_id=call.id,
                                )

                        # Track steps
                        for call in decision.tool_calls:
                            steps_completed.append(call.name)

                        iterations += 1

                        # Emit progress event
                        await self._emit_goal_event(goal_id, "progress", {
                            "iterations": iterations,
                            "steps": steps_completed[-5:],  # Last 5 steps
                            "timestamp": time.time(),
                        })
                    else:
                        # No tools, goal stuck
                        return GoalResult(
                            goal_id=goal_id,
                            success=False,
                            message="Could not determine actions for goal",
                            iterations=iterations,
                            steps_completed=steps_completed,
                            error="No tools selected",
                        )

        # Exceeded max iterations
        await self._emit_goal_event(goal_id, "failed", {
            "error": f"Exceeded {max_iters} iterations",
            "iterations": iterations,
            "timestamp": time.time(),
        })

        raise MaxIterationsError(max_iters)

    async def _emit_goal_event(
        self, goal_id: UUID, status: str, data: dict[str, Any]
    ) -> None:
        """Emit a goal status event."""
        await self._emitter.emit(
            f"goal.{status}",
            {"goal_id": str(goal_id), **data},
        )
