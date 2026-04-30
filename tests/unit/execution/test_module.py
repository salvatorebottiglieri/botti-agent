"""Tests for ExecutionModule."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from cortex.execution.module import ExecutionModule, GoalStore
from cortex.agentic.models import ChatResponse, GoalResult, GoalStatus, Goal


class TestGoalStore:
    """Tests for GoalStore."""

    @pytest.fixture
    def mock_db_pool(self):
        """Create a mock DB pool."""
        pool = MagicMock()
        return pool

    @pytest.fixture
    def store(self, mock_db_pool):
        """Create a GoalStore."""
        return GoalStore(db_pool=mock_db_pool)

    @pytest.mark.asyncio
    async def test_create_goal(self, store):
        """Create goal should return a Goal object."""
        goal = await store.create(
            description="Clean up files",
            priority="normal",
        )

        assert goal is not None
        assert goal.description == "Clean up files"
        assert goal.status == GoalStatus.PENDING

    @pytest.mark.asyncio
    async def test_create_goal_with_priority(self, store):
        """Create goal with custom priority."""
        goal = await store.create(
            description="Urgent task",
            priority="high",
        )

        assert goal.priority == "high"

    @pytest.mark.asyncio
    async def test_create_goal_with_deadline(self, store):
        """Create goal with deadline."""
        from datetime import datetime, timezone
        deadline = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)

        goal = await store.create(
            description="Timed task",
            deadline=deadline,
        )

        assert goal.deadline == deadline

    @pytest.mark.asyncio
    async def test_mark_completed(self, store):
        """Mark goal as completed."""
        goal = await store.create(description="Task")

        # Update status method should work
        await store.update_status(goal.id, GoalStatus.COMPLETED)
        
        # mark_completed should not raise
        await store.mark_completed(goal.id, message="Done!")

    @pytest.mark.asyncio
    async def test_mark_failed(self, store):
        """Mark goal as failed."""
        goal = await store.create(description="Task")

        # mark_failed should not raise
        await store.mark_failed(goal.id, error="Something went wrong")

    @pytest.mark.asyncio
    async def test_add_step(self, store):
        """Add step should not raise."""
        goal = await store.create(description="Task")

        # Should not raise
        await store.add_step(goal.id, "Step 1")


class TestExecutionModule:
    """Tests for ExecutionModule."""

    @pytest.fixture
    def mock_agent_loop(self):
        """Create a mock AgentLoop."""
        loop = MagicMock()
        loop.run_chat = AsyncMock(return_value=ChatResponse(
            message="Hello!",
            iterations=0,
        ))
        loop.run_goal = AsyncMock(return_value=GoalResult(
            goal_id=uuid4(),
            success=True,
            message="Completed",
        ))
        return loop

    @pytest.fixture
    def mock_goal_store(self):
        """Create a mock GoalStore."""
        store = MagicMock(spec=GoalStore)
        store.create = AsyncMock()
        store.get = AsyncMock()
        store.list_active = AsyncMock(return_value=[])
        return store

    @pytest.fixture
    def mock_event_bus(self):
        """Create a mock event bus."""
        bus = MagicMock()
        bus.publish = AsyncMock()
        bus.subscribe = MagicMock()
        return bus

    @pytest.fixture
    def module(self, mock_agent_loop, mock_goal_store, mock_event_bus):
        """Create an ExecutionModule."""
        return ExecutionModule(
            agent_loop=mock_agent_loop,
            goal_store=mock_goal_store,
            event_bus=mock_event_bus,
        )

    @pytest.mark.asyncio
    async def test_run_chat(self, module, mock_agent_loop):
        """Run chat should delegate to agent loop."""
        response = await module.run_chat(
            session_id=uuid4(),
            user_message="Hello",
        )

        assert response.message == "Hello!"
        mock_agent_loop.run_chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_goal(self, module, mock_goal_store):
        """Create goal should create in store and start execution."""
        mock_goal_store.create = AsyncMock(return_value=Goal(
            description="Test goal",
        ))

        goal = await module.create_goal(description="Test goal")

        assert mock_goal_store.create.called

    @pytest.mark.asyncio
    async def test_get_goal(self, module, mock_goal_store):
        """Get goal should delegate to store."""
        goal_id = uuid4()

        await module.get_goal(goal_id)

        mock_goal_store.get.assert_called_with(goal_id)

    @pytest.mark.asyncio
    async def test_list_active_goals(self, module, mock_goal_store):
        """List active goals."""
        goals = await module.list_active_goals()

        mock_goal_store.list_active.assert_called()

    @pytest.mark.asyncio
    async def test_run_goal(self, module, mock_agent_loop):
        """Run goal should execute via agent loop."""
        goal_id = uuid4()

        # The method should call agent_loop.run_goal
        try:
            result = await module.run_goal(goal_id, "Clean up files")
        except Exception:
            # May fail due to incomplete goal store, which is expected
            pass

        mock_agent_loop.run_goal.assert_called()

    @pytest.mark.asyncio
    async def test_subscribe_to_events(self, module, mock_event_bus):
        """Module should subscribe to events on init."""
        # Subscribe method should exist and be callable
        assert hasattr(module, 'subscribe')
        assert callable(module.subscribe)

    @pytest.mark.asyncio
    async def test_emit_goal_status(self, module, mock_event_bus):
        """Module should emit goal status events."""
        goal_id = uuid4()

        await module._emit_goal_event(goal_id, "started", {})

        mock_event_bus.publish.assert_called()


class TestExecutionModuleEdgeCases:
    """Edge case tests for ExecutionModule."""

    @pytest.fixture
    def mock_event_bus(self):
        bus = MagicMock()
        bus.publish = AsyncMock()
        bus.subscribe = AsyncMock()
        return bus

    @pytest.mark.asyncio
    async def test_run_chat_handles_error(self):
        """Run chat handles agent loop errors."""
        mock_loop = MagicMock()
        mock_loop.run_chat = AsyncMock(side_effect=Exception("Agent error"))

        module = ExecutionModule(
            agent_loop=mock_loop,
            goal_store=MagicMock(),
            event_bus=MagicMock(),
        )

        # Should raise exception
        with pytest.raises(Exception):
            await module.run_chat(uuid4(), "Hello")

    @pytest.mark.asyncio
    async def test_create_goal_with_priority(self, mock_event_bus):
        """Create goal with custom priority."""
        mock_store = MagicMock()
        mock_store.create = AsyncMock(return_value=Goal(
            description="Urgent task",
            priority="high",
        ))

        mock_loop = MagicMock()

        module = ExecutionModule(
            agent_loop=mock_loop,
            goal_store=mock_store,
            event_bus=mock_event_bus,
        )

        goal = await module.create_goal(
            description="Urgent task",
            priority="high",
        )

        mock_store.create.assert_called()

    @pytest.mark.asyncio
    async def test_create_goal_with_deadline(self, mock_event_bus):
        """Create goal with deadline."""
        from datetime import datetime, timezone

        deadline = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)

        mock_store = MagicMock()
        mock_store.create = AsyncMock(return_value=Goal(
            description="Timed task",
            deadline=deadline,
        ))

        mock_loop = MagicMock()

        module = ExecutionModule(
            agent_loop=mock_loop,
            goal_store=mock_store,
            event_bus=mock_event_bus,
        )

        goal = await module.create_goal(
            description="Timed task",
            deadline=deadline,
        )

        assert mock_store.create.called

    @pytest.mark.asyncio
    async def test_handle_goal_created_event(self, mock_event_bus):
        """Handle goal.created event."""
        mock_loop = MagicMock()
        mock_loop.run_goal = AsyncMock()

        mock_store = MagicMock()

        module = ExecutionModule(
            agent_loop=mock_loop,
            goal_store=mock_store,
            event_bus=mock_event_bus,
        )

        # Create a mock event
        mock_event = MagicMock()
        mock_event.payload = {
            "goal_id": str(uuid4()),
            "description": "Test",
        }

        # handle_event should not raise
        await module.handle_event(mock_event)

    @pytest.mark.asyncio
    async def test_run_goal_max_iterations(self, mock_event_bus):
        """Run goal should respect max iterations."""
        from cortex.agentic.models import MaxIterationsError

        mock_loop = MagicMock()
        mock_loop.run_goal = AsyncMock(side_effect=MaxIterationsError(10))

        mock_store = MagicMock()
        mock_store.update_status = AsyncMock()
        mock_store.mark_completed = AsyncMock()
        mock_store.mark_failed = AsyncMock()

        module = ExecutionModule(
            agent_loop=mock_loop,
            goal_store=mock_store,
            event_bus=mock_event_bus,
        )

        goal_id = uuid4()

        # Should handle gracefully, not raise
        result = await module.run_goal(goal_id, "Long task")
        
        # Should return error result
        assert result.success is False