"""Resume and cross-instance persistence tests for ExecutionModule.

Invariants under test (System Architecture):

1. A goal is never executed twice concurrently -- run_goal() on a RUNNING
   goal returns AlreadyRunning without invoking the loop.
2. On startup exactly the goals left `running` at shutdown become `pending`
   and are resumed; completed/failed/pending goals are untouched.
3. Goal state survives the process that created it -- get_goal() serves
   persisted rows, not process memory (a fresh repo-backed ExecutionModule
   can get_goal() a goal created by an earlier instance).
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from cortex.agentic.models import Goal, GoalResult, GoalStatus
from cortex.execution.module import ExecutionModule
from cortex.goals.repository import InMemoryGoalRepository


def _goal(description: str, status: GoalStatus, **kwargs) -> Goal:
    return Goal(description=description, status=status, **kwargs)


@pytest.fixture
def mock_loop():
    loop = MagicMock()
    loop.run_goal = AsyncMock(return_value=GoalResult(
        goal_id=uuid4(),
        success=True,
        message="Done",
    ))
    return loop


class TestRunGoalConcurrencyGuard:
    """Invariant 1: a RUNNING goal is never executed twice concurrently."""

    @pytest.mark.asyncio
    async def test_run_goal_on_running_goal_does_not_invoke_loop(self, mock_loop):
        """run_goal on a RUNNING goal returns AlreadyRunning without touching the loop."""
        repo = InMemoryGoalRepository()
        running = _goal("In flight", GoalStatus.RUNNING, started_at=100.0, created_at=1.0)
        await repo.create(running)

        module = ExecutionModule(agent_loop=mock_loop, goal_repository=repo)

        result = await module.run_goal(running.id, "In flight")

        assert result.success is False
        assert result.error == "AlreadyRunning"
        mock_loop.run_goal.assert_not_called()
        # The goal stays RUNNING: the background task still owns it.
        fetched = await repo.get(running.id)
        assert fetched.status == GoalStatus.RUNNING


class TestResumeInFlight:
    """Invariants 2 and 4: startup resume and repo-backed visibility."""

    @pytest.mark.asyncio
    async def test_resume_in_flight_touches_exactly_the_running_goals(self, mock_loop):
        """Running goals are marked pending (started_at reset) and re-run;
        completed/failed/pending goals are untouched."""
        repo = InMemoryGoalRepository()
        running_a = _goal("A", GoalStatus.RUNNING, started_at=100.0, created_at=1.0)
        running_b = _goal("B", GoalStatus.RUNNING, started_at=100.0, created_at=2.0)
        completed = _goal("C", GoalStatus.COMPLETED, completed_at=200.0, created_at=3.0)
        failed = _goal("D", GoalStatus.FAILED, completed_at=200.0, created_at=4.0)
        pending = _goal("E", GoalStatus.PENDING, created_at=5.0)
        for g in (running_a, running_b, completed, failed, pending):
            await repo.create(g)

        module = ExecutionModule(agent_loop=mock_loop, goal_repository=repo)
        await module.resume_in_flight()

        # Immediately after resume (no loop yield yet), the running goals are
        # marked PENDING with started_at reset; the others are untouched.
        assert running_a.status == GoalStatus.PENDING
        assert running_a.started_at is None
        assert running_b.status == GoalStatus.PENDING
        assert running_b.started_at is None
        assert completed.status == GoalStatus.COMPLETED
        assert failed.status == GoalStatus.FAILED
        assert pending.status == GoalStatus.PENDING

        # Let the scheduled background tasks execute.
        for _ in range(5):
            await asyncio.sleep(0)

        # The resumed goals were re-run to completion; the others never ran.
        assert running_a.status == GoalStatus.COMPLETED
        assert running_b.status == GoalStatus.COMPLETED
        assert completed.status == GoalStatus.COMPLETED
        assert failed.status == GoalStatus.FAILED
        assert pending.status == GoalStatus.PENDING

        loop_ids = [call.kwargs["goal_id"] for call in mock_loop.run_goal.call_args_list]
        assert set(loop_ids) == {running_a.id, running_b.id}

    @pytest.mark.asyncio
    async def test_resume_in_flight_noop_when_nothing_running(self, mock_loop):
        """With no running goals, resume schedules nothing."""
        repo = InMemoryGoalRepository()
        await repo.create(_goal("C", GoalStatus.COMPLETED, completed_at=1.0, created_at=1.0))
        await repo.create(_goal("P", GoalStatus.PENDING, created_at=2.0))

        module = ExecutionModule(agent_loop=mock_loop, goal_repository=repo)
        await module.resume_in_flight()
        for _ in range(3):
            await asyncio.sleep(0)

        mock_loop.run_goal.assert_not_called()


class TestCrossInstanceVisibility:
    """Invariant 4: goal state lives in the repository, not process memory."""

    @pytest.mark.asyncio
    async def test_fresh_module_reads_goal_created_by_earlier_instance(self, mock_loop):
        """A fresh repo-backed module can get_goal() a goal created by an
        earlier instance sharing the same repository."""
        repo = InMemoryGoalRepository()
        first = ExecutionModule(agent_loop=mock_loop, goal_repository=repo)
        goal = await first.create_goal(description="Created in an earlier process")

        second = ExecutionModule(agent_loop=MagicMock(), goal_repository=repo)
        fetched = await second.get_goal(goal.id)

        assert fetched is not None
        assert fetched.description == "Created in an earlier process"
        assert fetched.status == goal.status
        assert fetched.created_at == goal.created_at

    @pytest.mark.asyncio
    async def test_fresh_module_sees_status_transitions_of_earlier_module(self, mock_loop):
        """State mutated through one module's repo is visible to a fresh module."""
        repo = InMemoryGoalRepository()
        first = ExecutionModule(agent_loop=mock_loop, goal_repository=repo)
        goal = await first.create_goal(description="Shared state")

        # Transition the goal to RUNNING through the first module.
        await first.run_goal(goal.id, "Shared state")
        assert goal.status == GoalStatus.COMPLETED

        second = ExecutionModule(agent_loop=MagicMock(), goal_repository=repo)
        fetched = await second.get_goal(goal.id)

        assert fetched is not None
        assert fetched.status == GoalStatus.COMPLETED
        assert fetched.completed_at is not None


class TestGoalTaskRetention:
    """Background goal tasks are retained (not dropped) until they finish."""

    @pytest.mark.asyncio
    async def test_resume_in_flight_retains_spawned_tasks(self, mock_loop):
        """resume_in_flight keeps its background tasks in module._goal_tasks
        until they run to completion, then discards them."""
        repo = InMemoryGoalRepository()
        await repo.create(_goal("A", GoalStatus.RUNNING, started_at=1.0, created_at=1.0))
        await repo.create(_goal("B", GoalStatus.RUNNING, started_at=2.0, created_at=2.0))

        module = ExecutionModule(agent_loop=mock_loop, goal_repository=repo)
        await module.resume_in_flight()

        # Both resumed goals have a retained background task (not dropped).
        assert len(module._goal_tasks) == 2

        # After the event loop yields, both tasks finish and are discarded.
        for _ in range(5):
            await asyncio.sleep(0)
        assert len(module._goal_tasks) == 0

    @pytest.mark.asyncio
    async def test_create_goal_retains_spawned_task(self, mock_loop):
        """create_goal retains its background task until it completes."""
        repo = InMemoryGoalRepository()
        module = ExecutionModule(agent_loop=mock_loop, goal_repository=repo)

        await module.create_goal(description="Retained task")

        assert len(module._goal_tasks) == 1
        for _ in range(5):
            await asyncio.sleep(0)
        assert len(module._goal_tasks) == 0


class TestInMemoryGoalRepositoryUpdate:
    """InMemoryGoalRepository.update mirrors Postgres: a goal that was never
    created is a no-op (the SQL UPDATE affects zero rows), not an implicit
    insert."""

    @pytest.mark.asyncio
    async def test_update_unknown_goal_does_not_insert(self):
        repo = InMemoryGoalRepository()
        ghost = _goal("Ghost", GoalStatus.PENDING)

        await repo.update(ghost)

        assert await repo.get(ghost.id) is None
        assert repo._goals == {}
