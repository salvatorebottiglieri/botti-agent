"""Execution Module - wraps AgentLoop with goal lifecycle management."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from cortex.agentic.loop import AgentLoop
from cortex.agentic.models import (
    ChatResponse,
    Goal,
    GoalResult,
    GoalStatus,
    MaxIterationsError,
)
from cortex.events import EventEmitter
from cortex.goals.interfaces import GoalRepository

if TYPE_CHECKING:
    from cortex.agentic.events import LoopEvent
    from cortex.events import EventBus

logger = logging.getLogger(__name__)


class ExecutionModule:
    """
    Execution Module wraps the AgentLoop.

    Goals are persisted through a `GoalRepository` (Postgres-backed in
    production, in-memory by default) so goal state survives restarts;
    goals left `running` at shutdown are resumed on startup by
    `resume_in_flight()`.

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
        goal_repository: GoalRepository | None = None,
    ):
        # Imported lazily so importers of execution.module do not pull in
        # the in-memory implementation (and its asyncpg dependency chain)
        # when a Postgres-backed repository is injected instead.
        from cortex.goals.repository import InMemoryGoalRepository

        self._agent_loop = agent_loop
        self._event_bus = event_bus
        self._emitter = EventEmitter(event_bus, source_module="execution_module")
        self._goal_repository = goal_repository or InMemoryGoalRepository()
        self._goal_results: dict[UUID, GoalResult] = {}
        # Strong references to background goal tasks so they are never
        # garbage-collected before running (the dropped-task anti-pattern);
        # each task removes itself once it finishes.
        self._goal_tasks: set[asyncio.Task[None]] = set()

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

    async def stream_chat(
        self,
        session_id: UUID,
        user_message: str,
        *,
        max_iterations: int | None = None,
    ) -> AsyncGenerator[LoopEvent, None]:
        """
        Stream chat progress events as a transparent passthrough.

        Delegates to ``self._agent_loop.stream_chat(...)`` unchanged and
        yields events as-is — including the yield-then-reraise error
        contract: ``MaxIterationsError`` (and any other exception) is NOT
        swallowed, unlike ``run_chat()``'s fallback.

        Args:
            session_id: Current session
            user_message: User's message
            max_iterations: Optional iteration limit

        Yields:
            LoopEvent progress signals (thinking, tool_start, tool_done,
            text, done, error).
        """
        async for event in self._agent_loop.stream_chat(
            session_id=session_id,
            user_message=user_message,
            max_iterations=max_iterations,
        ):
            yield event

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
        await self._goal_repository.create(goal)

        # Start the goal in the background. No "goal.created" event is
        # emitted here: this module subscribes to it, so emitting would
        # start the goal twice (once synchronously via handle_event while
        # the POST is still in flight, once via the task below).
        self._spawn_goal_task(goal)

        return goal

    async def get_goal(self, goal_id: UUID) -> Goal | None:
        """Get a goal by ID."""
        return await self._goal_repository.get(goal_id)

    async def get_goal_result(self, goal_id: UUID) -> GoalResult | None:
        """Get the terminal result of a finished goal, if any."""
        return self._goal_results.get(goal_id)

    async def list_active_goals(self) -> list[Goal]:
        """List active goals (pending, running, or paused)."""
        return await self._goal_repository.list_active()

    async def resume_in_flight(self) -> None:
        """
        Resume goals left running at shutdown.

        Goals with status RUNNING are marked PENDING (started_at reset),
        persisted, and re-scheduled for execution. Direct startup call —
        not routed through the event bus.
        """
        goals = await self._goal_repository.get_in_flight()
        for goal in goals:
            goal.status = GoalStatus.PENDING
            goal.started_at = None
            await self._goal_repository.update(goal)
            self._spawn_goal_task(goal)

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
        goal = await self._goal_repository.get(goal_id)
        if goal is None:
            goal = Goal(
                id=goal_id,
                description=description,
                status=GoalStatus.PENDING,
                created_at=time.time(),
            )
            await self._goal_repository.create(goal)

        # Guard against concurrent starts (e.g. an external goal.created
        # event racing the background task): a goal already running is
        # never started twice.
        if goal.status == GoalStatus.RUNNING:
            return GoalResult(
                goal_id=goal_id,
                success=False,
                message="Goal already running",
                error="AlreadyRunning",
            )

        goal.status = GoalStatus.RUNNING
        goal.started_at = time.time()
        await self._goal_repository.update(goal)

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
            await self._goal_repository.update(goal)

            await self._emit_goal_event(goal_id, "completed", {
                "message": result.message,
                "iterations": result.iterations,
                "timestamp": goal.completed_at,
            })

            # Keep the terminal result so GET /goals/{id} can report the
            # actual message/iterations, not just the goal metadata.
            self._goal_results[goal_id] = result

            return result

        except MaxIterationsError:
            goal.status = GoalStatus.FAILED
            goal.error = "Max iterations exceeded"
            goal.completed_at = time.time()
            await self._goal_repository.update(goal)

            await self._emit_goal_event(goal_id, "failed", {
                "error": goal.error,
                "timestamp": goal.completed_at,
            })

            result = GoalResult(
                goal_id=goal_id,
                success=False,
                message="Goal exceeded maximum iterations",
                error="MaxIterationsError",
            )
            self._goal_results[goal_id] = result

            return result

        except Exception as e:
            goal.status = GoalStatus.FAILED
            goal.error = str(e)
            goal.completed_at = time.time()
            await self._goal_repository.update(goal)

            await self._emit_goal_event(goal_id, "failed", {
                "error": goal.error,
                "timestamp": goal.completed_at,
            })

            result = GoalResult(
                goal_id=goal_id,
                success=False,
                message="Goal failed",
                error=str(e),
            )
            self._goal_results[goal_id] = result

            return result

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

    def _spawn_goal_task(self, goal: Goal) -> asyncio.Task[None]:
        """Start a goal's background task, keeping a strong reference to it.

        Without the reference the task could be garbage-collected before it
        runs, leaving the goal stuck PENDING forever. The done-callback
        discards the reference once the task finishes.
        """
        task = asyncio.create_task(self._run_goal_async(goal))
        self._goal_tasks.add(task)
        task.add_done_callback(self._goal_tasks.discard)
        return task

    async def _run_goal_async(self, goal: Goal) -> None:
        """Background task to run a goal."""
        try:
            await self.run_goal(goal.id, goal.description)
        except Exception as e:
            logger.error(f"Background goal {goal.id} failed: {e}")

    async def _emit_goal_event(
        self, goal_id: UUID, status: str, data: dict[str, Any]
    ) -> None:
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
