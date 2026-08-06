"""Execution Module - wraps AgentLoop with goal lifecycle management."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from cortex.agentic.models import (
    ChatResponse,
    Goal,
    GoalResult,
    GoalStatus,
    MaxIterationsError,
)
from cortex.agentic.loop import AgentLoop
from cortex.events import EventEmitter

if TYPE_CHECKING:
    from cortex.events import EventBus

logger = logging.getLogger(__name__)


_ACTIVE_STATUSES = {GoalStatus.PENDING, GoalStatus.RUNNING, GoalStatus.PAUSED}


class ExecutionModule:
    """
    Execution Module wraps the AgentLoop.

    Goals live in process memory (`self._goals`). They are not durable across
    restarts; a real persistence adapter is a future feature, not a stubbed
    facade pretending to be one.

    Handles:
    - Chat mode execution
    - Goal lifecycle management
    - Event subscriptions

    Subscribes to:
    - goal.created
    - recommendation.executed

    Emits:
    - goal.status
    - goal.completed
    - goal.failed
    """

    def __init__(
        self,
        agent_loop: AgentLoop,
        event_bus: EventBus | None = None,
    ):
        self._agent_loop = agent_loop
        self._event_bus = event_bus
        self._emitter = EventEmitter(event_bus, source_module="execution_module")
        self._goals: dict[UUID, Goal] = {}

    async def run_chat(
        self,
        session_id: UUID,
        user_message: str,
        *,
        max_iterations: int | None = None,
    ) -> ChatResponse:
        """
        Run a chat interaction.

        Args:
            session_id: Current session
            user_message: User's message
            max_iterations: Optional iteration limit

        Returns:
            ChatResponse
        """
        try:
            return await self._agent_loop.run_chat(
                session_id=session_id,
                user_message=user_message,
                max_iterations=max_iterations,
            )
        except MaxIterationsError:
            return ChatResponse(
                message="I ran out of iterations. Please try a simpler request.",
                iterations=max_iterations or 20,
            )

    async def create_goal(
        self,
        description: str,
        priority: str = "normal",
        deadline: Any | None = None,
    ) -> Goal:
        """
        Create a goal and start execution.

        Args:
            description: Goal description
            priority: Goal priority
            deadline: Optional deadline

        Returns:
            The created Goal
        """
        goal = Goal(
            id=uuid4(),
            description=description,
            priority=priority,
            deadline=deadline,
            status=GoalStatus.PENDING,
            created_at=time.time(),
        )
        self._goals[goal.id] = goal

        await self._emit_goal_event(goal.id, "created", {
            "description": description,
            "priority": priority,
            "timestamp": goal.created_at,
        })

        asyncio.create_task(self._run_goal_async(goal))

        return goal

    async def get_goal(self, goal_id: UUID) -> Goal | None:
        """Get a goal by ID."""
        return self._goals.get(goal_id)

    async def list_active_goals(self) -> list[Goal]:
        """List active goals (pending, running, or paused)."""
        return [g for g in self._goals.values() if g.status in _ACTIVE_STATUSES]

    async def run_goal(
        self,
        goal_id: UUID,
        description: str,
        *,
        max_iterations: int | None = None,
    ) -> GoalResult:
        """
        Run a specific goal.

        If the goal isn't already tracked (e.g. the call arrived via an external
        `goal.created` event), a Goal entry is created on the fly so its state
        is queryable afterwards.

        Args:
            goal_id: Goal identifier
            description: Goal description
            max_iterations: Optional iteration limit

        Returns:
            GoalResult
        """
        goal = self._goals.get(goal_id)
        if goal is None:
            goal = Goal(
                id=goal_id,
                description=description,
                status=GoalStatus.PENDING,
                created_at=time.time(),
            )
            self._goals[goal_id] = goal

        goal.status = GoalStatus.RUNNING
        goal.started_at = time.time()

        await self._emit_goal_event(goal_id, "started", {
            "description": description,
            "timestamp": goal.started_at,
        })

        try:
            result = await self._agent_loop.run_goal(
                goal_id=goal_id,
                description=description,
                max_iterations=max_iterations,
            )

            goal.status = GoalStatus.COMPLETED
            goal.completed_at = time.time()

            await self._emit_goal_event(goal_id, "completed", {
                "message": result.message,
                "iterations": result.iterations,
                "timestamp": goal.completed_at,
            })

            return result

        except MaxIterationsError:
            goal.status = GoalStatus.FAILED
            goal.error = "Max iterations exceeded"
            goal.completed_at = time.time()

            await self._emit_goal_event(goal_id, "failed", {
                "error": goal.error,
                "timestamp": goal.completed_at,
            })

            return GoalResult(
                goal_id=goal_id,
                success=False,
                message="Goal exceeded maximum iterations",
                error="MaxIterationsError",
            )

        except Exception as e:
            goal.status = GoalStatus.FAILED
            goal.error = str(e)
            goal.completed_at = time.time()

            await self._emit_goal_event(goal_id, "failed", {
                "error": goal.error,
                "timestamp": goal.completed_at,
            })

            return GoalResult(
                goal_id=goal_id,
                success=False,
                message="Goal failed",
                error=str(e),
            )

    async def handle_event(self, event: Any) -> None:
        """
        Handle subscribed events.

        Events:
        - goal.created: Start goal execution
        - recommendation.executed: Log recommendation execution
        """
        event_type = getattr(event, 'type', None) or getattr(event, 'event_type', None)
        payload = getattr(event, 'payload', {})

        if event_type == "goal.created":
            goal_id = payload.get("goal_id")
            description = payload.get("description", "")

            if goal_id:
                try:
                    await self.run_goal(UUID(goal_id), description)
                except Exception as e:
                    logger.error(f"Failed to run goal {goal_id}: {e}")

        elif event_type == "recommendation.executed":
            # Log recommendation execution
            logger.info(f"Recommendation executed: {payload}")

    async def _run_goal_async(self, goal: Goal) -> None:
        """Background task to run a goal."""
        try:
            await self.run_goal(goal.id, goal.description)
        except Exception as e:
            logger.error(f"Background goal {goal.id} failed: {e}")

    async def _emit_goal_event(self, goal_id: UUID, status: str, data: dict) -> None:
        """Emit a goal status event."""
        await self._emitter.emit(
            f"goal.{status}",
            {"goal_id": str(goal_id), **data},
        )

    async def subscribe(self) -> None:
        """Subscribe to events."""
        if self._event_bus:
            await self._event_bus.subscribe("goal.created", self.handle_event)
            await self._event_bus.subscribe("recommendation.executed", self.handle_event)