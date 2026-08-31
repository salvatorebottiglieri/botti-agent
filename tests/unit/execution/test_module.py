"""Tests for ExecutionModule."""

from datetime import UTC
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from cortex.agentic.events import (
    ErrorEvent,
    ResponseDoneEvent,
    TextDeltaEvent,
    ThinkingEvent,
    ToolStartEvent,
)
from cortex.agentic.models import ChatResponse, GoalResult, GoalStatus, MaxIterationsError
from cortex.execution.module import ExecutionModule


class TestExecutionModule:
    """Tests for ExecutionModule."""

    @pytest.fixture
    def mock_agent_loop(self):
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
    def mock_event_bus(self):
        bus = MagicMock()
        bus.publish = AsyncMock()
        bus.subscribe = MagicMock()
        return bus

    @pytest.fixture
    def module(self, mock_agent_loop, mock_event_bus):
        return ExecutionModule(
            agent_loop=mock_agent_loop,
            event_bus=mock_event_bus,
        )

    @pytest.mark.asyncio
    async def test_run_chat(self, module, mock_agent_loop):
        """Run chat delegates to agent loop."""
        response = await module.run_chat(
            session_id=uuid4(),
            user_message="Hello",
        )

        assert response.message == "Hello!"
        mock_agent_loop.run_chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_goal_returns_queryable_goal(self, module):
        """After create_goal, the goal must be queryable via get_goal."""
        goal = await module.create_goal(description="Test goal")

        assert goal is not None
        assert goal.description == "Test goal"
        assert goal.status == GoalStatus.PENDING

        fetched = await module.get_goal(goal.id)
        assert fetched is goal

    @pytest.mark.asyncio
    async def test_create_goal_with_priority_and_deadline(self, module):
        from datetime import datetime
        deadline = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)

        goal = await module.create_goal(
            description="Urgent task",
            priority="high",
            deadline=deadline,
        )

        assert goal.priority == "high"
        assert goal.deadline == deadline

    @pytest.mark.asyncio
    async def test_get_goal_returns_none_for_unknown_id(self, module):
        result = await module.get_goal(uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_list_active_goals_excludes_terminal(self, mock_agent_loop, mock_event_bus):
        """list_active_goals returns only pending/running/paused goals."""
        module = ExecutionModule(
            agent_loop=mock_agent_loop,
            event_bus=mock_event_bus,
        )

        pending = await module.create_goal(description="Pending goal")
        # Mutate one to a terminal state directly via the held Goal object.
        terminal = await module.create_goal(description="Finished goal")
        terminal.status = GoalStatus.COMPLETED

        active = await module.list_active_goals()
        active_ids = {g.id for g in active}

        assert pending.id in active_ids
        assert terminal.id not in active_ids

    @pytest.mark.asyncio
    async def test_run_goal_mutates_goal_state(self, mock_event_bus):
        """run_goal sets status to RUNNING then COMPLETED on success."""
        mock_loop = MagicMock()
        mock_loop.run_goal = AsyncMock(return_value=GoalResult(
            goal_id=uuid4(),
            success=True,
            message="Done",
        ))

        module = ExecutionModule(
            agent_loop=mock_loop,
            event_bus=mock_event_bus,
        )

        goal = await module.create_goal(description="Task")
        await module.run_goal(goal.id, "Task")

        # The Goal entry held in the dict reflects the terminal state.
        fetched = await module.get_goal(goal.id)
        assert fetched.status == GoalStatus.COMPLETED
        assert fetched.started_at is not None
        assert fetched.completed_at is not None

    @pytest.mark.asyncio
    async def test_run_goal_keeps_terminal_result(self, mock_event_bus):
        """run_goal stores the terminal GoalResult so the API can report the
        actual message and iterations, not just goal metadata."""
        mock_loop = MagicMock()
        mock_loop.run_goal = AsyncMock(return_value=GoalResult(
            goal_id=uuid4(),
            success=True,
            message="Found 3 TODOs",
            iterations=4,
            steps_completed=["grep", "file_read"],
        ))

        module = ExecutionModule(
            agent_loop=mock_loop,
            event_bus=mock_event_bus,
        )

        goal = await module.create_goal(description="Find TODOs")
        await module.run_goal(goal.id, "Find TODOs")

        result = await module.get_goal_result(goal.id)

        assert result is not None
        assert result.success is True
        assert result.message == "Found 3 TODOs"
        assert result.iterations == 4
        assert result.steps_completed == ["grep", "file_read"]

    @pytest.mark.asyncio
    async def test_run_goal_keeps_failed_result(self, mock_event_bus):
        """A failed goal also keeps its result (with the error)."""
        mock_loop = MagicMock()
        mock_loop.run_goal = AsyncMock(side_effect=RuntimeError("boom"))

        module = ExecutionModule(
            agent_loop=mock_loop,
            event_bus=mock_event_bus,
        )

        goal = await module.create_goal(description="Doomed")
        await module.run_goal(goal.id, "Doomed")

        result = await module.get_goal_result(goal.id)

        assert result is not None
        assert result.success is False
        assert result.error == "boom"

    @pytest.mark.asyncio
    async def test_get_goal_result_none_for_unknown(self, mock_event_bus):
        """get_goal_result returns None for a goal that never ran."""
        module = ExecutionModule(
            agent_loop=MagicMock(),
            event_bus=mock_event_bus,
        )

        assert await module.get_goal_result(uuid4()) is None

    @pytest.mark.asyncio
    async def test_run_goal_marks_failed_on_exception(self, mock_event_bus):
        mock_loop = MagicMock()
        mock_loop.run_goal = AsyncMock(side_effect=RuntimeError("boom"))

        module = ExecutionModule(
            agent_loop=mock_loop,
            event_bus=mock_event_bus,
        )

        goal = await module.create_goal(description="Doomed")
        result = await module.run_goal(goal.id, "Doomed")

        assert result.success is False
        fetched = await module.get_goal(goal.id)
        assert fetched.status == GoalStatus.FAILED
        assert fetched.error == "boom"

    @pytest.mark.asyncio
    async def test_run_goal_max_iterations(self, mock_event_bus):
        mock_loop = MagicMock()
        mock_loop.run_goal = AsyncMock(side_effect=MaxIterationsError(10))

        module = ExecutionModule(
            agent_loop=mock_loop,
            event_bus=mock_event_bus,
        )

        goal = await module.create_goal(description="Long task")
        result = await module.run_goal(goal.id, "Long task")

        assert result.success is False
        fetched = await module.get_goal(goal.id)
        assert fetched.status == GoalStatus.FAILED

    @pytest.mark.asyncio
    async def test_run_goal_creates_entry_for_unknown_id(self, mock_event_bus):
        """run_goal called with an unknown id (e.g. from an external event)
        registers the goal so it's queryable afterwards."""
        mock_loop = MagicMock()
        mock_loop.run_goal = AsyncMock(return_value=GoalResult(
            goal_id=uuid4(),
            success=True,
            message="Done",
        ))

        module = ExecutionModule(
            agent_loop=mock_loop,
            event_bus=mock_event_bus,
        )

        external_id = uuid4()
        await module.run_goal(external_id, "External goal")

        fetched = await module.get_goal(external_id)
        assert fetched is not None
        assert fetched.status == GoalStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_subscribe_to_events(self, module):
        assert hasattr(module, 'subscribe')
        assert callable(module.subscribe)

    @pytest.mark.asyncio
    async def test_emit_goal_status(self, module, mock_event_bus):
        await module._emit_goal_event(uuid4(), "started", {})
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
        mock_loop = MagicMock()
        mock_loop.run_chat = AsyncMock(side_effect=Exception("Agent error"))

        module = ExecutionModule(
            agent_loop=mock_loop,
            event_bus=MagicMock(),
        )

        with pytest.raises(Exception):
            await module.run_chat(uuid4(), "Hello")

    @pytest.mark.asyncio
    async def test_handle_goal_created_event(self, mock_event_bus):
        mock_loop = MagicMock()
        mock_loop.run_goal = AsyncMock(return_value=GoalResult(
            goal_id=uuid4(),
            success=True,
            message="Done",
        ))

        module = ExecutionModule(
            agent_loop=mock_loop,
            event_bus=mock_event_bus,
        )

        mock_event = MagicMock()
        mock_event.payload = {
            "goal_id": str(uuid4()),
            "description": "Test",
        }
        mock_event.type = "goal.created"

        # Should not raise
        await module.handle_event(mock_event)

    @pytest.mark.asyncio
    async def test_create_goal_does_not_emit_goal_created(self, mock_event_bus):
        """create_goal must not publish goal.created: this module subscribes
        to that type, so emitting would start the goal twice."""
        mock_loop = MagicMock()
        mock_loop.run_goal = AsyncMock(return_value=GoalResult(
            goal_id=uuid4(),
            success=True,
            message="Done",
        ))
        module = ExecutionModule(
            agent_loop=mock_loop,
            event_bus=mock_event_bus,
        )

        await module.create_goal(description="Test goal")

        published_types = [
            call.args[0].type if hasattr(call.args[0], "type") else None
            for call in mock_event_bus.publish.call_args_list
        ]
        assert "goal.created" not in published_types

    @pytest.mark.asyncio
    async def test_run_goal_guard_skips_running_goal(self, mock_event_bus):
        """run_goal on an already-RUNNING goal returns without re-running:
        the background task owns the execution."""
        mock_loop = MagicMock()
        mock_loop.run_goal = AsyncMock(return_value=GoalResult(
            goal_id=uuid4(),
            success=True,
            message="Done",
        ))

        module = ExecutionModule(
            agent_loop=mock_loop,
            event_bus=mock_event_bus,
        )

        goal = await module.create_goal(description="Task")
        goal.status = GoalStatus.RUNNING  # simulate a task already in flight

        result = await module.run_goal(goal.id, "Task")

        assert result.success is False
        assert result.error == "AlreadyRunning"
        # The loop must not have been asked to run a second time.
        mock_loop.run_goal.assert_not_called()


class TestExecutionModuleStreamChat:
    """ExecutionModule.stream_chat transparent passthrough (user story 7)."""

    @pytest.mark.asyncio
    async def test_stream_chat_passes_events_through_unchanged(self):
        """Events pass through in order, unchanged, with arguments delegated."""
        session_id = uuid4()
        scripted = [
            ThinkingEvent(session_id=session_id, message="reasoning"),
            ToolStartEvent(session_id=session_id, tool_name="web_search", tool_call_id="call_1"),
            TextDeltaEvent(session_id=session_id, delta="hi"),
            ResponseDoneEvent(
                session_id=session_id,
                message="hi",
                tools_used=["web_search"],
                iterations=1,
            ),
        ]

        class FakeLoop:
            calls: list[tuple[UUID, str, int | None, bool]] = []

            async def stream_chat(
                self,
                session_id: UUID,
                user_message: str,
                *,
                max_iterations: int | None = None,
                stream: bool = False,
            ):
                FakeLoop.calls.append((session_id, user_message, max_iterations, stream))
                for event in scripted:
                    yield event

        module = ExecutionModule(agent_loop=FakeLoop())

        seen = [event async for event in module.stream_chat(session_id, "hello")]

        assert seen == scripted
        assert FakeLoop.calls == [(session_id, "hello", None, False)]

    @pytest.mark.asyncio
    async def test_stream_chat_forwards_stream_flag_to_loop(self):
        """The `stream` flag is passed through to the agent loop unchanged."""
        session_id = uuid4()

        class FakeLoop:
            calls: list[bool] = []

            async def stream_chat(
                self,
                session_id: UUID,
                user_message: str,
                *,
                max_iterations: int | None = None,
                stream: bool = False,
            ):
                FakeLoop.calls.append(stream)
                if False:
                    yield  # make this an async generator

        module = ExecutionModule(agent_loop=FakeLoop())

        _ = [e async for e in module.stream_chat(session_id, "hi", stream=True)]
        _ = [e async for e in module.stream_chat(session_id, "hi", stream=False)]

        assert FakeLoop.calls == [True, False]

    @pytest.mark.asyncio
    async def test_stream_chat_reraises_max_iterations_after_error_event(self):
        """The yield-then-reraise contract survives the passthrough: an
        ErrorEvent is yielded, then MaxIterationsError propagates (unlike
        run_chat's fallback)."""
        session_id = uuid4()

        class FakeLoop:
            async def stream_chat(
                self,
                session_id: UUID,
                user_message: str,
                *,
                max_iterations: int | None = None,
                stream: bool = False,
            ):
                yield ErrorEvent(
                    session_id=session_id,
                    error="Max iterations exceeded",
                    code="max_iterations",
                )
                raise MaxIterationsError(max_iterations or 20)

        module = ExecutionModule(agent_loop=FakeLoop())

        seen: list[ErrorEvent] = []
        with pytest.raises(MaxIterationsError):
            async for event in module.stream_chat(session_id, "hello", max_iterations=3):
                seen.append(event)

        assert len(seen) == 1
        assert isinstance(seen[0], ErrorEvent)
        assert seen[0].code == "max_iterations"
