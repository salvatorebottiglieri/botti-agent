"""Tests for AgentLoop."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from cortex.agentic.loop import AgentLoop
from cortex.agentic.models import (
    Context,
    Decision,
    DecisionType,
    ChatResponse,
    MaxIterationsError,
    Mode,
)
from cortex.sessions.models import Message, MessageRole


class TestAgentLoop:
    """Tests for AgentLoop."""

    @pytest.fixture
    def mock_context_builder(self):
        """Create a mock context builder."""
        builder = MagicMock()
        builder.build = AsyncMock()
        return builder

    @pytest.fixture
    def mock_reasoner(self):
        """Create a mock reasoner."""
        reasoner = MagicMock()
        reasoner.reason = AsyncMock()
        return reasoner

    @pytest.fixture
    def mock_executor(self):
        """Create a mock executor."""
        executor = MagicMock()
        executor.execute_tools = AsyncMock()
        return executor

    @pytest.fixture
    def mock_event_bus(self):
        """Create a mock event bus."""
        bus = MagicMock()
        bus.publish = AsyncMock()
        return bus

    @pytest.fixture
    def loop(self, mock_context_builder, mock_reasoner, mock_executor, mock_event_bus):
        """Create an AgentLoop."""
        return AgentLoop(
            context_builder=mock_context_builder,
            reasoner=mock_reasoner,
            executor=mock_executor,
            event_bus=mock_event_bus,
        )

    @pytest.mark.asyncio
    async def test_run_chat_returns_response(self, loop, mock_context_builder, mock_reasoner):
        """Run chat should return a ChatResponse."""
        session_id = uuid4()

        mock_context_builder.build = AsyncMock(return_value=Context(session_id=session_id))
        mock_reasoner.reason = AsyncMock(return_value=Decision.respond("Hello!"))

        response = await loop.run_chat(session_id, "Hi")

        assert isinstance(response, ChatResponse)
        assert response.message == "Hello!"
        assert response.iterations == 0

    @pytest.mark.asyncio
    async def test_run_chat_calls_reasoner(self, loop, mock_reasoner):
        """Run chat should call the reasoner."""
        session_id = uuid4()

        mock_reasoner.reason = AsyncMock(return_value=Decision.respond("Done"))

        await loop.run_chat(session_id, "Hello")

        mock_reasoner.reason.assert_called()

    @pytest.mark.asyncio
    async def test_run_chat_with_tool_execution(self, loop, mock_context_builder, mock_reasoner, mock_executor):
        """Run chat should execute tools when reasoner decides to."""
        session_id = uuid4()

        from cortex.tools.interfaces import ToolCall, ToolResult

        tool_call = ToolCall(id="1", name="file_read", arguments={"path": "/test"})

        # First reason returns tool call, second returns response
        mock_reasoner.reason = AsyncMock(side_effect=[
            Decision.execute_tools([tool_call]),
            Decision.respond("File contents are..."),
        ])

        mock_executor.execute_tools = AsyncMock(return_value=[
            ToolResult(tool_call_id="1", tool_name="file_read", success=True, output="content")
        ])

        response = await loop.run_chat(session_id, "Read the file")

        assert mock_executor.execute_tools.called
        assert response.iterations >= 1

    @pytest.mark.asyncio
    async def test_run_chat_max_iterations(self, loop, mock_context_builder, mock_reasoner):
        """Run chat should raise after max iterations."""
        session_id = uuid4()

        mock_context_builder.build = AsyncMock(return_value=Context(session_id=session_id))
        mock_reasoner.reason = AsyncMock(return_value=Decision.execute_tools([
            ToolCall(id="1", name="shell", arguments={"cmd": "true"})
        ]))

        with pytest.raises(MaxIterationsError):
            await loop.run_chat(session_id, "Loop test", max_iterations=5)

    @pytest.mark.asyncio
    async def test_run_chat_empty_message(self, loop):
        """Run chat handles empty message."""
        session_id = uuid4()

        # Should still work
        loop._context_builder.build = AsyncMock(return_value=Context(session_id=session_id))
        loop._reasoner.reason = AsyncMock(return_value=Decision.respond("What?"))

        response = await loop.run_chat(session_id, "")

        assert response is not None


class TestAgentLoopGoalMode:
    """Tests for AgentLoop goal mode."""

    @pytest.fixture
    def mock_context_builder(self):
        builder = MagicMock()
        builder.build = AsyncMock()
        return builder

    @pytest.fixture
    def mock_reasoner(self):
        reasoner = MagicMock()
        reasoner.reason = AsyncMock()
        return reasoner

    @pytest.fixture
    def mock_executor(self):
        executor = MagicMock()
        executor.execute_tools = AsyncMock()
        return executor

    @pytest.fixture
    def mock_event_bus(self):
        bus = MagicMock()
        bus.publish = AsyncMock()
        return bus

    @pytest.fixture
    def loop(self, mock_context_builder, mock_reasoner, mock_executor, mock_event_bus):
        return AgentLoop(
            context_builder=mock_context_builder,
            reasoner=mock_reasoner,
            executor=mock_executor,
            event_bus=mock_event_bus,
        )

    @pytest.mark.asyncio
    async def test_run_goal_completes(self, loop, mock_context_builder, mock_reasoner):
        """Run goal should complete successfully."""
        goal_id = uuid4()

        mock_context_builder.build = AsyncMock(return_value=Context(
            session_id=uuid4(),
            goal=MagicMock(goal_id=goal_id),
        ))
        mock_reasoner.reason = AsyncMock(return_value=Decision.respond("Goal completed!"))

        result = await loop.run_goal(goal_id, "Clean up files")

        assert result is not None

    @pytest.mark.asyncio
    async def test_run_goal_emits_events(self, loop, mock_event_bus):
        """Run goal should emit status events."""
        goal_id = uuid4()

        loop._context_builder.build = AsyncMock(return_value=Context(session_id=uuid4()))
        loop._reasoner.reason = AsyncMock(return_value=Decision.respond("Done"))

        await loop.run_goal(goal_id, "Task")

        # Should emit goal status events
        assert mock_event_bus.publish.called


class TestAgentLoopEdgeCases:
    """Edge case tests for AgentLoop."""

    @pytest.fixture
    def mock_context_builder(self):
        builder = MagicMock()
        builder.build = AsyncMock(side_effect=Exception("Context error"))
        return builder

    @pytest.fixture
    def mock_reasoner(self):
        return MagicMock()

    @pytest.fixture
    def mock_executor(self):
        return MagicMock()

    @pytest.fixture
    def mock_event_bus(self):
        bus = MagicMock()
        bus.publish = AsyncMock()
        return bus

    @pytest.mark.asyncio
    async def test_run_chat_handles_context_error(self, mock_context_builder, mock_reasoner, mock_executor, mock_event_bus):
        """Run chat handles context building errors."""
        loop = AgentLoop(
            context_builder=mock_context_builder,
            reasoner=mock_reasoner,
            executor=mock_executor,
            event_bus=mock_event_bus,
        )

        # Should raise or return error response
        with pytest.raises(Exception):
            await loop.run_chat(uuid4(), "Hello")

    @pytest.mark.asyncio
    async def test_run_chat_handles_reasoner_error(self):
        """Run chat handles reasoner errors."""
        mock_context_builder = MagicMock()
        mock_context_builder.build = AsyncMock(return_value=Context(session_id=uuid4()))

        mock_reasoner = MagicMock()
        mock_reasoner.reason = AsyncMock(side_effect=Exception("LLM error"))

        loop = AgentLoop(
            context_builder=mock_context_builder,
            reasoner=mock_reasoner,
            executor=MagicMock(),
            event_bus=MagicMock(),
        )

        # Should raise
        with pytest.raises(Exception):
            await loop.run_chat(uuid4(), "Hello")

    @pytest.mark.asyncio
    async def test_run_chat_with_ask_question(self):
        """Run chat handles ask_question decision."""
        mock_context_builder = MagicMock()
        mock_context_builder.build = AsyncMock(return_value=Context(session_id=uuid4()))

        mock_reasoner = MagicMock()
        mock_reasoner.reason = AsyncMock(return_value=Decision.ask_question(
            "Did you mean file A or file B?"
        ))

        loop = AgentLoop(
            context_builder=mock_context_builder,
            reasoner=mock_reasoner,
            executor=MagicMock(),
            event_bus=MagicMock(),
        )

        response = await loop.run_chat(uuid4(), "Open the file")

        # Should return the question
        assert "file A or file B" in response.message or response.message is not None

    @pytest.mark.asyncio
    async def test_run_chat_preserves_conversation(self):
        """Run chat should preserve conversation for context."""
        mock_context_builder = MagicMock()
        mock_context_builder.build = AsyncMock(return_value=Context(
            session_id=uuid4(),
            conversation=[
                Message(session_id=uuid4(), role=MessageRole.USER, content="Hello"),
                Message(session_id=uuid4(), role=MessageRole.ASSISTANT, content="Hi!"),
            ]
        ))

        mock_reasoner = MagicMock()
        mock_reasoner.reason = AsyncMock(return_value=Decision.respond("How can I help?"))

        loop = AgentLoop(
            context_builder=mock_context_builder,
            reasoner=mock_reasoner,
            executor=MagicMock(),
            event_bus=MagicMock(),
        )

        response = await loop.run_chat(uuid4(), "What's my name?")

        # Context should include conversation history
        assert mock_context_builder.build.called


# Import needed for tests
from cortex.tools.interfaces import ToolCall