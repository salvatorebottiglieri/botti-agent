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

if TYPE_CHECKING:
    from cortex.events import EventBus

logger = logging.getLogger(__name__)


class GoalStore:
    """
    Persistence layer for goals.

    Uses the database pool to store goal state.
    """

    def __init__(self, db_pool: Any):
        self._db = db_pool

    async def create(
        self,
        description: str,
        priority: str = "normal",
        deadline: Any | None = None,
    ) -> Goal:
        """Create a new goal."""
        goal = Goal(
            id=uuid4(),
            description=description,
            priority=priority,
            deadline=deadline,
            status=GoalStatus.PENDING,
            created_at=time.time(),
        )

        # TODO: Persist to database
        # await self._db.execute("INSERT INTO goals ...")

        return goal

    async def get(self, goal_id: UUID) -> Goal | None:
        """Get a goal by ID."""
        # TODO: Fetch from database
        return None

    async def update_status(self, goal_id: UUID, status: GoalStatus) -> None:
        """Update goal status."""
        # TODO: Update in database
        pass

    async def list_active(self) -> list[Goal]:
        """List active (pending/running) goals."""
        # TODO: Fetch from database
        return []

    async def mark_completed(self, goal_id: UUID, message: str = "") -> None:
        """Mark a goal as completed."""
        await self.update_status(goal_id, GoalStatus.COMPLETED)

    async def mark_failed(self, goal_id: UUID, error: str) -> None:
        """Mark a goal as failed."""
        goal = await self.get(goal_id)
        if goal:
            goal.error = error
        await self.update_status(goal_id, GoalStatus.FAILED)

    async def add_step(self, goal_id: UUID, step: str) -> None:
        """Add a completed step to a goal."""
        # TODO: Update in database
        pass


class ExecutionModule:
    """
    Execution Module wraps the AgentLoop.

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
        goal_store: GoalStore | None = None,
        event_bus: EventBus | None = None,
    ):
        self._agent_loop = agent_loop
        self._goal_store = goal_store or GoalStore(db_pool=None)
        self._event_bus = event_bus

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
        # Create goal in store
        goal = await self._goal_store.create(description, priority, deadline)

        # Emit started event
        await self._emit_goal_event(goal.id, "created", {
            "description": description,
            "priority": priority,
            "timestamp": time.time(),
        })

        # Start background execution
        asyncio.create_task(self._run_goal_async(goal))

        return goal

    async def get_goal(self, goal_id: UUID) -> Goal | None:
        """Get a goal by ID."""
        return await self._goal_store.get(goal_id)

    async def list_active_goals(self) -> list[Goal]:
        """List active goals."""
        return await self._goal_store.list_active()

    async def run_goal(
        self,
        goal_id: UUID,
        description: str,
        *,
        max_iterations: int | None = None,
    ) -> GoalResult:
        """
        Run a specific goal.

        Args:
            goal_id: Goal identifier
            description: Goal description
            max_iterations: Optional iteration limit

        Returns:
            GoalResult
        """
        # Update status to running
        await self._goal_store.update_status(goal_id, GoalStatus.RUNNING)

        await self._emit_goal_event(goal_id, "started", {
            "description": description,
            "timestamp": time.time(),
        })

        try:
            result = await self._agent_loop.run_goal(
                goal_id=goal_id,
                description=description,
                max_iterations=max_iterations,
            )

            # Mark completed
            await self._goal_store.mark_completed(goal_id, result.message)

            await self._emit_goal_event(goal_id, "completed", {
                "message": result.message,
                "iterations": result.iterations,
                "timestamp": time.time(),
            })

            return result

        except MaxIterationsError:
            await self._goal_store.mark_failed(goal_id, "Max iterations exceeded")

            await self._emit_goal_event(goal_id, "failed", {
                "error": "Max iterations exceeded",
                "timestamp": time.time(),
            })

            return GoalResult(
                goal_id=goal_id,
                success=False,
                message="Goal exceeded maximum iterations",
                error="MaxIterationsError",
            )

        except Exception as e:
            await self._goal_store.mark_failed(goal_id, str(e))

            await self._emit_goal_event(goal_id, "failed", {
                "error": str(e),
                "timestamp": time.time(),
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
        if not self._event_bus:
            return

        try:
            from cortex.events import BaseEvent
            event = BaseEvent.create(
                event_type=f"goal.{status}",
                payload={
                    "goal_id": str(goal_id),
                    **data,
                },
                source_module="execution_module"
            )
            await self._event_bus.publish(event)
        except Exception as e:
            logger.warning(f"Failed to emit goal event: {e}")

    def subscribe(self) -> None:
        """Subscribe to events."""
        if self._event_bus:
            self._event_bus.subscribe("goal.created", self.handle_event)
            self._event_bus.subscribe("recommendation.executed", self.handle_event)