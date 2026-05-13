"""AgentLoop - Core agentic loop: Think → Act → Observe → Respond."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from cortex.agentic.models import (
    ChatResponse,
    Decision,
    DecisionType,
    GoalResult,
    GoalStatus,
    MaxIterationsError,
    Mode,
)
from cortex.events import EventEmitter
from cortex.sessions.models import Message, MessageRole

if TYPE_CHECKING:
    from cortex.agentic.context_builder import ContextBuilder
    from cortex.agentic.reasoner import Reasoner
    from cortex.agentic.executor import LoopExecutor
    from cortex.events import EventBus

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
        max_chat_iterations: int = 20,
        max_goal_iterations: int = 100,
    ):
        self._context_builder = context_builder
        self._reasoner = reasoner
        self._executor = executor
        self._emitter = EventEmitter(event_bus, source_module="agent_loop")
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

        Args:
            session_id: Current session
            user_message: The user's message
            max_iterations: Optional override for max iterations

        Returns:
            ChatResponse with message and metadata

        Raises:
            MaxIterationsError: If loop exceeds max iterations
        """
        max_iters = max_iterations or self._max_chat_iterations
        iterations = 0
        tools_used: list[str] = []
        messages: list[Message] = []

        # Add user message
        messages.append(Message(
            session_id=session_id,
            role=MessageRole.USER,
            content=user_message,
        ))

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

            # Handle decision
            match decision.decision_type:
                case DecisionType.RESPOND:
                    # Done! Return the response
                    return ChatResponse(
                        message=decision.text or "I'm not sure how to respond.",
                        iterations=iterations,
                        tools_used=tools_used,
                        session_id=session_id,
                    )

                case DecisionType.ASK_QUESTION:
                    # Return the question
                    return ChatResponse(
                        message=decision.text or "Could you clarify?",
                        iterations=iterations,
                        tools_used=tools_used,
                        session_id=session_id,
                    )

                case DecisionType.EXECUTE_TOOLS:
                    # 3. Act - execute tools
                    if decision.tool_calls:
                        results = await self._executor.execute_tools(decision.tool_calls)

                        # Track tools used
                        for call in decision.tool_calls:
                            tools_used.append(call.name)

                        # 4. Observe - add tool results to conversation
                        for call, result in zip(decision.tool_calls, results):
                            msg = Message(
                                session_id=session_id,
                                role=MessageRole.TOOL_RESULT,
                                content=result.output if result.success else f"Error: {result.error}",
                            )
                            messages.append(msg)

                        iterations += 1
                    else:
                        # No tools to execute, respond
                        return ChatResponse(
                            message="I couldn't determine what tools to use.",
                            iterations=iterations,
                            tools_used=tools_used,
                            session_id=session_id,
                        )

        # Exceeded max iterations
        raise MaxIterationsError(max_iters)

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
                        results = await self._executor.execute_tools(decision.tool_calls)

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

    async def _emit_goal_event(self, goal_id: UUID, status: str, data: dict) -> None:
        """Emit a goal status event."""
        await self._emitter.emit(
            f"goal.{status}",
            {"goal_id": str(goal_id), **data},
        )