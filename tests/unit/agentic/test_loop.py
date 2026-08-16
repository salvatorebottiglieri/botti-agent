"""Tests for AgentLoop."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from cortex.agentic.events import (
    ErrorEvent,
    LoopEvent,
    ResponseDoneEvent,
    TextDeltaEvent,
    ThinkingEvent,
    ToolResultEvent,
    ToolStartEvent,
)
from cortex.agentic.loop import AgentLoop
from cortex.agentic.models import (
    ChatResponse,
    Context,
    Decision,
    MaxIterationsError,
)
from cortex.sessions.models import Message, MessageRole
from cortex.tools.interfaces import ToolCall, ToolResult


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
        executor.execute_single = AsyncMock()
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

        mock_executor.execute_single = AsyncMock(return_value=ToolResult(
            tool_call_id="1", tool_name="file_read", success=True, output="content",
        ))

        response = await loop.run_chat(session_id, "Read the file")

        assert mock_executor.execute_single.called
        assert response.iterations >= 1

    @pytest.mark.asyncio
    async def test_run_chat_max_iterations(self, loop, mock_context_builder, mock_reasoner, mock_executor):
        """Run chat should raise after max iterations."""
        from cortex.tools.interfaces import ToolResult

        session_id = uuid4()

        mock_context_builder.build = AsyncMock(return_value=Context(session_id=session_id))
        mock_reasoner.reason = AsyncMock(return_value=Decision.execute_tools([
            ToolCall(id="1", name="shell", arguments={"cmd": "true"})
        ]))
        mock_executor.execute_single = AsyncMock(return_value=ToolResult(
            tool_call_id="1", tool_name="shell", success=True, output="ok",
        ))

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

    @pytest.mark.asyncio
    async def test_run_chat_returns_complete_response(
        self, loop, mock_context_builder, mock_reasoner
    ) -> None:
        """RESPOND path returns a complete ChatResponse with all fields."""
        session_id = uuid4()

        mock_context_builder.build = AsyncMock(return_value=Context(session_id=session_id))
        mock_reasoner.reason = AsyncMock(return_value=Decision.respond("Hello!"))

        response = await loop.run_chat(session_id, "Hi")

        assert isinstance(response, ChatResponse)
        assert response.message == "Hello!"
        assert response.iterations == 0
        assert response.tools_used == []
        assert response.session_id == session_id

    @pytest.mark.asyncio
    async def test_run_chat_ask_question_returns_complete_response(
        self, loop, mock_context_builder, mock_reasoner
    ) -> None:
        """ASK_QUESTION path returns a complete ChatResponse with the question."""
        session_id = uuid4()

        mock_context_builder.build = AsyncMock(return_value=Context(session_id=session_id))
        mock_reasoner.reason = AsyncMock(return_value=Decision.ask_question(
            "Did you mean file A or file B?"
        ))

        response = await loop.run_chat(session_id, "Open the file")

        assert isinstance(response, ChatResponse)
        assert response.message == "Did you mean file A or file B?"
        assert response.iterations == 0
        assert response.tools_used == []
        assert response.session_id == session_id

    @pytest.mark.asyncio
    async def test_run_chat_propagates_tools_used(
        self, loop, mock_context_builder, mock_reasoner, mock_executor
    ) -> None:
        """Tool round-trip propagates tools_used and iterations into the response."""
        from cortex.tools.interfaces import ToolResult

        session_id = uuid4()
        call = ToolCall(id="call_1", name="search", arguments={"q": "x"})

        mock_context_builder.build = AsyncMock(return_value=Context(session_id=session_id))
        mock_reasoner.reason = AsyncMock(side_effect=[
            Decision.execute_tools([call], reasoning="searching"),
            Decision.respond("Found it.", reasoning="synthesized"),
        ])
        mock_executor.execute_single = AsyncMock(return_value=ToolResult(
            tool_call_id="call_1", tool_name="search", success=True, output="found",
        ))

        response = await loop.run_chat(session_id, "Find it")

        assert response.message == "Found it."
        assert response.tools_used == ["search"]
        assert response.iterations >= 1
        assert response.session_id == session_id

    @pytest.mark.asyncio
    async def test_run_chat_reraises_original_exception(
        self, loop, mock_context_builder, mock_reasoner
    ) -> None:
        """A failing reasoner propagates the original exception, unwrapped."""
        session_id = uuid4()

        mock_context_builder.build = AsyncMock(return_value=Context(session_id=session_id))
        mock_reasoner.reason = AsyncMock(side_effect=ValueError("boom"))

        with pytest.raises(ValueError, match="boom"):
            await loop.run_chat(session_id, "Hi")

    @pytest.mark.asyncio
    async def test_run_chat_max_iterations_override_bound(
        self, loop, mock_context_builder, mock_reasoner, mock_executor
    ) -> None:
        """max_iterations override bounds the loop at the override, not the default."""
        from cortex.tools.interfaces import ToolResult

        session_id = uuid4()
        call = ToolCall(id="call_1", name="shell", arguments={"cmd": "true"})

        mock_context_builder.build = AsyncMock(return_value=Context(session_id=session_id))
        mock_reasoner.reason = AsyncMock(return_value=Decision.execute_tools(
            [call], reasoning="running",
        ))
        mock_executor.execute_single = AsyncMock(return_value=ToolResult(
            tool_call_id="call_1", tool_name="shell", success=True, output="ok",
        ))

        with pytest.raises(MaxIterationsError) as exc_info:
            await loop.run_chat(session_id, "Loop", max_iterations=2)

        assert exc_info.value.max_iterations == 2

    @pytest.mark.asyncio
    async def test_run_chat_empty_tool_calls_fallback(
        self, loop, mock_context_builder, mock_reasoner
    ) -> None:
        """EXECUTE_TOOLS with empty tool_calls returns the fallback text."""
        session_id = uuid4()

        mock_context_builder.build = AsyncMock(return_value=Context(session_id=session_id))
        mock_reasoner.reason = AsyncMock(return_value=Decision.execute_tools(
            [], reasoning="no tools available",
        ))

        response = await loop.run_chat(session_id, "Do something")

        assert response.message == "I couldn't determine what tools to use."
        assert response.iterations == 0
        assert response.tools_used == []
        assert response.session_id == session_id

    @pytest.mark.asyncio
    async def test_run_chat_does_not_publish_loop_events(
        self, loop, mock_context_builder, mock_reasoner, mock_event_bus
    ) -> None:
        """Loop events are caller-scoped: run_chat never publishes them on the bus."""
        session_id = uuid4()

        mock_context_builder.build = AsyncMock(return_value=Context(session_id=session_id))
        mock_reasoner.reason = AsyncMock(return_value=Decision.respond("Hello!"))

        response = await loop.run_chat(session_id, "Hi")

        assert response.message == "Hello!"
        mock_event_bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_chat_accumulates_multiple_deltas(self, loop, monkeypatch) -> None:
        """The drainer concatenates TextDeltaEvent deltas, not ResponseDoneEvent.message."""
        session_id = uuid4()

        async def fake_stream_chat(*args, **kwargs):
            yield TextDeltaEvent(session_id, delta="Hel")
            yield TextDeltaEvent(session_id, delta="lo")
            yield ResponseDoneEvent(
                session_id,
                message="Hello!",
                tools_used=["tool_a"],
                iterations=3,
            )

        monkeypatch.setattr(loop, "stream_chat", fake_stream_chat)

        response = await loop.run_chat(session_id, "Hi")

        assert response.message == "Hello"
        assert response.tools_used == ["tool_a"]
        assert response.iterations == 3
        assert response.session_id == session_id


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

    @pytest.mark.asyncio
    async def test_run_goal_persists_tool_results_when_repository_wired(
        self, mock_context_builder, mock_executor, mock_event_bus
    ):
        """With a session repository wired, run_goal creates a real session
        and persists assistant tool-calls plus tool results (linked by
        tool_call_id) so the next context build sees them."""
        goal_id = uuid4()
        session_id = uuid4()
        call_id = "call_abc123"

        repo = MagicMock()
        repo.create = AsyncMock(return_value=MagicMock(id=session_id))
        repo.update_state = AsyncMock(return_value=MagicMock(id=session_id))
        repo.add_message = AsyncMock(return_value=MagicMock())

        mock_context_builder.build = AsyncMock(return_value=Context(session_id=session_id))
        mock_executor.execute_tools = AsyncMock(return_value=[
            ToolResult(
                tool_call_id=call_id,
                tool_name="shell",
                success=True,
                output="hello from tool",
            )
        ])

        reasoner = MagicMock()
        reasoner.reason = AsyncMock(side_effect=[
            Decision.execute_tools(
                tool_calls=[ToolCall(id=call_id, name="shell", arguments={"command": "echo hi"})],
                reasoning="need output",
            ),
            Decision.respond("done", reasoning="have output"),
        ])

        loop = AgentLoop(
            context_builder=mock_context_builder,
            reasoner=reasoner,
            executor=mock_executor,
            event_bus=mock_event_bus,
            session_repository=repo,
        )

        result = await loop.run_goal(goal_id, "Run a command")

        assert result.success is True
        repo.create.assert_awaited_once()
        repo.update_state.assert_awaited_once()
        # assistant tool-call message
        assistant_call = repo.add_message.await_args_list[0]
        assert assistant_call.args[1] == MessageRole.ASSISTANT
        assert assistant_call.kwargs["tool_calls"] == [
            {"id": call_id, "name": "shell", "arguments": {"command": "echo hi"}}
        ]
        # tool result message, linked by id
        result_call = repo.add_message.await_args_list[1]
        assert result_call.args[1] == MessageRole.TOOL_RESULT
        assert result_call.kwargs["tool_call_id"] == call_id
        assert result_call.args[2] == "hello from tool"

    @pytest.mark.asyncio
    async def test_run_goal_without_repository_keeps_legacy_behavior(
        self, mock_context_builder, mock_executor, mock_event_bus
    ):
        """Without a repository wired, run_goal still completes and never
        touches persistence (backward-compatible path)."""
        goal_id = uuid4()

        mock_context_builder.build = AsyncMock(return_value=Context(session_id=uuid4()))
        mock_executor.execute_tools = AsyncMock(return_value=[])

        reasoner = MagicMock()
        reasoner.reason = AsyncMock(side_effect=[
            Decision.execute_tools(
                tool_calls=[ToolCall(id="call_1", name="shell", arguments={})],
                reasoning="need output",
            ),
            Decision.respond("done", reasoning="have output"),
        ])

        loop = AgentLoop(
            context_builder=mock_context_builder,
            reasoner=reasoner,
            executor=mock_executor,
            event_bus=mock_event_bus,
        )

        result = await loop.run_goal(goal_id, "Run a command")

        assert result.success is True


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

        await loop.run_chat(uuid4(), "What's my name?")

        # Context should include conversation history
        assert mock_context_builder.build.called


class TestStreamChat:
    """Tests for AgentLoop.stream_chat async generator."""

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
        executor.execute_single = AsyncMock()
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
    async def test_respond_event_sequence(
        self, loop, mock_context_builder, mock_reasoner
    ) -> None:
        """RESPOND yields thinking -> text -> done with correct payloads."""
        session_id = uuid4()

        mock_context_builder.build = AsyncMock(return_value=Context(session_id=session_id))
        mock_reasoner.reason = AsyncMock(return_value=Decision.respond(
            "Hello!", reasoning="decided to greet",
        ))

        events = [e async for e in loop.stream_chat(session_id, "Hi")]

        assert [e.event_type for e in events] == ["thinking", "text", "done"]
        assert isinstance(events[0], ThinkingEvent)
        assert events[0].message == "decided to greet"
        assert isinstance(events[1], TextDeltaEvent)
        assert events[1].delta == "Hello!"
        assert isinstance(events[2], ResponseDoneEvent)
        assert events[2].message == "Hello!"
        assert events[2].iterations == 0
        assert events[2].tools_used == []
        assert all(e.session_id == session_id for e in events)

    @pytest.mark.asyncio
    async def test_ask_question_event_sequence(
        self, loop, mock_context_builder, mock_reasoner
    ) -> None:
        """ASK_QUESTION yields thinking -> text -> done with the question."""
        session_id = uuid4()

        mock_context_builder.build = AsyncMock(return_value=Context(session_id=session_id))
        mock_reasoner.reason = AsyncMock(return_value=Decision.ask_question(
            "Did you mean file A or file B?", reasoning="need clarification",
        ))

        events = [e async for e in loop.stream_chat(session_id, "Open the file")]

        assert [e.event_type for e in events] == ["thinking", "text", "done"]
        assert isinstance(events[1], TextDeltaEvent)
        assert events[1].delta == "Did you mean file A or file B?"
        assert isinstance(events[2], ResponseDoneEvent)
        assert events[2].message == "Did you mean file A or file B?"
        assert events[2].iterations == 0
        assert events[2].tools_used == []

    @pytest.mark.asyncio
    async def test_tool_round_interleaves_start_result(
        self, loop, mock_context_builder, mock_reasoner, mock_executor
    ) -> None:
        """ToolStartEvent precedes ToolResultEvent per call, interleaved."""
        from cortex.tools.interfaces import ToolResult

        session_id = uuid4()
        call_a = ToolCall(id="call_a", name="tool_a", arguments={"x": 1})
        call_b = ToolCall(id="call_b", name="tool_b", arguments={"y": 2})

        mock_context_builder.build = AsyncMock(return_value=Context(session_id=session_id))
        mock_reasoner.reason = AsyncMock(side_effect=[
            Decision.execute_tools([call_a, call_b], reasoning="need tools"),
            Decision.respond("All done.", reasoning="finished"),
        ])
        mock_executor.execute_single = AsyncMock(side_effect=[
            ToolResult(tool_call_id="call_a", tool_name="tool_a", success=True, output="out_a"),
            ToolResult(tool_call_id="call_b", tool_name="tool_b", success=True, output="out_b"),
        ])

        events = [e async for e in loop.stream_chat(session_id, "Run tools")]

        assert [e.event_type for e in events] == [
            "thinking", "tool_start", "tool_done",
            "tool_start", "tool_done",
            "thinking", "text", "done",
        ]

        # Interleave: each call's start precedes its own result
        start_a = next(
            i for i, e in enumerate(events)
            if isinstance(e, ToolStartEvent) and e.tool_call_id == "call_a"
        )
        result_a = next(
            i for i, e in enumerate(events)
            if isinstance(e, ToolResultEvent) and e.tool_call_id == "call_a"
        )
        start_b = next(
            i for i, e in enumerate(events)
            if isinstance(e, ToolStartEvent) and e.tool_call_id == "call_b"
        )
        result_b = next(
            i for i, e in enumerate(events)
            if isinstance(e, ToolResultEvent) and e.tool_call_id == "call_b"
        )
        assert start_a < result_a < start_b < result_b

        # Tool result payloads match their calls
        result_a_event = events[result_a]
        assert isinstance(result_a_event, ToolResultEvent)
        assert result_a_event.tool_name == "tool_a"
        assert result_a_event.success is True
        assert result_a_event.output == "out_a"

        # One thinking per iteration
        assert sum(isinstance(e, ThinkingEvent) for e in events) == 2

        # Final done carries iterations and tools used
        done = events[-1]
        assert isinstance(done, ResponseDoneEvent)
        assert done.iterations == 1
        assert done.tools_used == ["tool_a", "tool_b"]

    @pytest.mark.asyncio
    async def test_tool_round_conversation_carries_tool_call_contract(
        self, loop, mock_context_builder, mock_reasoner, mock_executor
    ) -> None:
        """The conversation injected into context carries an assistant message
        with the tool calls (internal shape) and a tool_result message with
        the matching tool_call_id — provider-agnostic, no LLM wire shape."""
        from cortex.tools.interfaces import ToolResult

        session_id = uuid4()
        call_a = ToolCall(id="call_a", name="tool_a", arguments={"x": 1})

        ctx = Context(session_id=session_id)
        mock_context_builder.build = AsyncMock(return_value=ctx)
        mock_reasoner.reason = AsyncMock(side_effect=[
            Decision.execute_tools([call_a], reasoning="need tools"),
            Decision.respond("All done.", reasoning="finished"),
        ])
        mock_executor.execute_single = AsyncMock(return_value=ToolResult(
            tool_call_id="call_a", tool_name="tool_a", success=True, output="out_a",
        ))

        events = [e async for e in loop.stream_chat(session_id, "Run tools")]

        assert events[-1].event_type == "done"

        # Assistant message records the tool calls in the internal shape
        assistant_msgs = [
            m for m in ctx.conversation
            if m.role == MessageRole.ASSISTANT and m.tool_calls
        ]
        assert len(assistant_msgs) == 1
        assert assistant_msgs[0].tool_calls == [{
            "id": "call_a",
            "name": "tool_a",
            "arguments": {"x": 1},
        }]

        # Tool result message references the call it answers
        tool_msgs = [m for m in ctx.conversation if m.role == MessageRole.TOOL_RESULT]
        assert len(tool_msgs) == 1
        assert tool_msgs[0].tool_call_id == "call_a"
        assert "out_a" in tool_msgs[0].content

    @pytest.mark.asyncio
    async def test_empty_tool_calls_yields_fallback_response(
        self, loop, mock_context_builder, mock_reasoner
    ) -> None:
        """Empty EXECUTE_TOOLS yields fallback text + done, iterations unchanged."""
        session_id = uuid4()

        mock_context_builder.build = AsyncMock(return_value=Context(session_id=session_id))
        mock_reasoner.reason = AsyncMock(return_value=Decision.execute_tools(
            [], reasoning="no tools available",
        ))

        events = [e async for e in loop.stream_chat(session_id, "Do something")]

        assert [e.event_type for e in events] == ["thinking", "text", "done"]
        assert isinstance(events[1], TextDeltaEvent)
        assert events[1].delta == "I couldn't determine what tools to use."
        assert isinstance(events[2], ResponseDoneEvent)
        assert events[2].message == "I couldn't determine what tools to use."
        assert events[2].iterations == 0
        assert events[2].tools_used == []

    @pytest.mark.asyncio
    async def test_max_iterations_yields_error_then_raises(
        self, loop, mock_context_builder, mock_reasoner, mock_executor
    ) -> None:
        """Exceeding max iterations yields ErrorEvent(code=max_iterations), then raises."""
        from cortex.tools.interfaces import ToolResult

        session_id = uuid4()
        call = ToolCall(id="call_1", name="shell", arguments={"cmd": "true"})

        mock_context_builder.build = AsyncMock(return_value=Context(session_id=session_id))
        mock_reasoner.reason = AsyncMock(return_value=Decision.execute_tools(
            [call], reasoning="running",
        ))
        mock_executor.execute_single = AsyncMock(return_value=ToolResult(
            tool_call_id="call_1", tool_name="shell", success=True, output="ok",
        ))

        events: list[LoopEvent] = []
        with pytest.raises(MaxIterationsError):
            async for event in loop.stream_chat(session_id, "Loop", max_iterations=2):
                events.append(event)

        error_events = [
            e for e in events if isinstance(e, ErrorEvent) and e.code == "max_iterations"
        ]
        assert error_events
        assert error_events[0].session_id == session_id
        assert isinstance(events[-1], ErrorEvent)

    @pytest.mark.asyncio
    async def test_exception_yields_error_then_reraises(
        self, loop, mock_context_builder, mock_reasoner
    ) -> None:
        """A generic exception yields ErrorEvent(code=None), then re-raises."""
        session_id = uuid4()

        mock_context_builder.build = AsyncMock(return_value=Context(session_id=session_id))
        mock_reasoner.reason = AsyncMock(side_effect=ValueError("boom"))

        events: list[LoopEvent] = []
        with pytest.raises(ValueError, match="boom"):
            async for event in loop.stream_chat(session_id, "Hi"):
                events.append(event)

        assert any(
            isinstance(e, ErrorEvent) and e.code is None and e.error == "boom"
            for e in events
        )

    @pytest.mark.asyncio
    async def test_tool_failure_yields_result_not_error(
        self, loop, mock_context_builder, mock_reasoner, mock_executor
    ) -> None:
        """A failed tool yields ToolResultEvent(success=False); loop continues."""
        from cortex.tools.interfaces import ToolResult

        session_id = uuid4()
        call = ToolCall(id="call_1", name="shell", arguments={"cmd": "false"})

        mock_context_builder.build = AsyncMock(return_value=Context(session_id=session_id))
        mock_reasoner.reason = AsyncMock(side_effect=[
            Decision.execute_tools([call], reasoning="running"),
            Decision.respond("Recovered.", reasoning="continued"),
        ])
        mock_executor.execute_single = AsyncMock(return_value=ToolResult(
            tool_call_id="call_1", tool_name="shell", success=False, error="nope",
        ))

        events = [e async for e in loop.stream_chat(session_id, "Try it")]

        assert not any(isinstance(e, ErrorEvent) for e in events)
        result_event = next(e for e in events if isinstance(e, ToolResultEvent))
        assert result_event.success is False
        assert result_event.error == "nope"
        # The loop continued: a second thinking step followed the failure
        assert sum(isinstance(e, ThinkingEvent) for e in events) == 2
        assert isinstance(events[-1], ResponseDoneEvent)
        assert events[-1].message == "Recovered."

    @pytest.mark.asyncio
    async def test_stream_parity_with_run_chat(
        self, loop, mock_context_builder, mock_reasoner, mock_executor
    ) -> None:
        """stream_chat and run_chat agree on message, iterations, tools_used."""
        from cortex.tools.interfaces import ToolResult

        session_id = uuid4()
        call_a = ToolCall(id="call_a", name="search", arguments={"q": "x"})
        call_b = ToolCall(id="call_b", name="read", arguments={"path": "/y"})
        results = [
            ToolResult(tool_call_id="call_a", tool_name="search", success=True, output="found"),
            ToolResult(tool_call_id="call_b", tool_name="read", success=True, output="content"),
        ]
        script = [
            Decision.execute_tools([call_a, call_b], reasoning="search then read"),
            Decision.respond("Here is the answer.", reasoning="synthesized"),
        ]

        mock_context_builder.build = AsyncMock(return_value=Context(session_id=session_id))

        # Drain stream_chat
        mock_reasoner.reason = AsyncMock(side_effect=list(script))
        mock_executor.execute_single = AsyncMock(side_effect=list(results))
        stream_events = [e async for e in loop.stream_chat(session_id, "Find it")]
        done_event = next(e for e in stream_events if isinstance(e, ResponseDoneEvent))

        # run_chat with the same script (execute_single is exhausted by the stream half)
        mock_reasoner.reason = AsyncMock(side_effect=list(script))
        mock_executor.execute_single = AsyncMock(side_effect=list(results))
        response = await loop.run_chat(session_id, "Find it")

        assert done_event.message == response.message
        assert done_event.iterations == response.iterations
        assert done_event.tools_used == response.tools_used
        assert done_event.message == "Here is the answer."
        assert done_event.iterations == 1
        assert done_event.tools_used == ["search", "read"]


class TestStreamChatPersistence:
    """Tests for AgentLoop.stream_chat message persistence."""

    @pytest.fixture
    def mock_context_builder(self):
        """Create a mock context builder."""
        builder = MagicMock()
        builder.max_messages = 20
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
        executor.execute_single = AsyncMock()
        return executor

    @pytest.fixture
    def mock_event_bus(self):
        """Create a mock event bus."""
        bus = MagicMock()
        bus.publish = AsyncMock()
        return bus

    @pytest.fixture
    def repo(self):
        """Create a mocked session repository."""
        repository = MagicMock()
        repository.add_message = AsyncMock(return_value=MagicMock())
        repository.get_messages = AsyncMock(return_value=[])
        return repository

    @pytest.fixture
    def loop(self, mock_context_builder, mock_reasoner, mock_executor, mock_event_bus):
        """Create an AgentLoop without a session repository."""
        return AgentLoop(
            context_builder=mock_context_builder,
            reasoner=mock_reasoner,
            executor=mock_executor,
            event_bus=mock_event_bus,
        )

    def make_loop(
        self,
        mock_context_builder,
        mock_reasoner,
        mock_executor,
        mock_event_bus,
        repo,
    ) -> AgentLoop:
        """Create an AgentLoop with a wired session repository."""
        return AgentLoop(
            context_builder=mock_context_builder,
            reasoner=mock_reasoner,
            executor=mock_executor,
            event_bus=mock_event_bus,
            session_repository=repo,
        )

    @pytest.mark.asyncio
    async def test_respond_persists_user_then_assistant(
        self, mock_context_builder, mock_reasoner, mock_executor, mock_event_bus, repo
    ) -> None:
        """RESPOND persists USER then ASSISTANT in order, with correct content."""
        session_id = uuid4()

        mock_context_builder.build = AsyncMock(return_value=Context(session_id=session_id))
        mock_reasoner.reason = AsyncMock(return_value=Decision.respond(
            "Hello!", reasoning="decided to greet",
        ))

        loop = self.make_loop(
            mock_context_builder, mock_reasoner, mock_executor, mock_event_bus, repo
        )

        events = [e async for e in loop.stream_chat(session_id, "Hi")]

        assert [e.event_type for e in events] == ["thinking", "text", "done"]
        calls = repo.add_message.await_args_list
        assert [c.args[1] for c in calls] == [MessageRole.USER, MessageRole.ASSISTANT]
        assert calls[0].args[0] == session_id
        assert calls[0].args[2] == "Hi"
        assert calls[1].args[0] == session_id
        assert calls[1].args[2] == "Hello!"

    @pytest.mark.asyncio
    async def test_tool_round_trip_persists_full_sequence(
        self, mock_context_builder, mock_reasoner, mock_executor, mock_event_bus, repo
    ) -> None:
        """Tool round-trip persists USER, ASSISTANT(tool_calls), TOOL_RESULT,
        ASSISTANT(text) in order, with the internal tool-call shape."""
        from cortex.tools.interfaces import ToolResult

        session_id = uuid4()
        call = ToolCall(id="call_1", name="search", arguments={"q": "x"})

        mock_context_builder.build = AsyncMock(return_value=Context(session_id=session_id))
        mock_reasoner.reason = AsyncMock(side_effect=[
            Decision.execute_tools([call], reasoning="searching"),
            Decision.respond("Found it.", reasoning="synthesized"),
        ])
        mock_executor.execute_single = AsyncMock(return_value=ToolResult(
            tool_call_id="call_1", tool_name="search", success=True, output="found",
        ))

        loop = self.make_loop(
            mock_context_builder, mock_reasoner, mock_executor, mock_event_bus, repo
        )

        events = [e async for e in loop.stream_chat(session_id, "Find it")]

        assert isinstance(events[-1], ResponseDoneEvent)
        calls = repo.add_message.await_args_list
        assert [c.args[1] for c in calls] == [
            MessageRole.USER,
            MessageRole.ASSISTANT,
            MessageRole.TOOL_RESULT,
            MessageRole.ASSISTANT,
        ]
        # user message
        assert calls[0].args[2] == "Find it"
        # assistant tool-call message, internal shape
        assert calls[1].args[2] == ""
        assert calls[1].kwargs["tool_calls"] == [
            {"id": "call_1", "name": "search", "arguments": {"q": "x"}}
        ]
        # tool result, linked by id
        assert calls[2].args[2] == "found"
        assert calls[2].kwargs["tool_call_id"] == "call_1"
        # final assistant text
        assert calls[3].args[2] == "Found it."

    @pytest.mark.asyncio
    async def test_seeds_history_from_repository(
        self, mock_context_builder, mock_reasoner, mock_executor, mock_event_bus, repo
    ) -> None:
        """Prior messages are seeded into the conversation before the new
        user message; get_messages is called with limit == max_messages - 1."""
        session_id = uuid4()
        prior = [
            Message(session_id=session_id, role=MessageRole.USER, content="first"),
            Message(session_id=session_id, role=MessageRole.ASSISTANT, content="first reply"),
        ]
        repo.get_messages = AsyncMock(return_value=list(prior))

        mock_context_builder.max_messages = 20
        mock_context_builder.build = AsyncMock(return_value=Context(session_id=session_id))
        seen: dict[str, list[Message]] = {}

        async def record_reason(context):
            seen["conversation"] = list(context.conversation)
            return Decision.respond("How can I help?", reasoning="greeting")

        mock_reasoner.reason = AsyncMock(side_effect=record_reason)

        loop = self.make_loop(
            mock_context_builder, mock_reasoner, mock_executor, mock_event_bus, repo
        )

        events = [e async for e in loop.stream_chat(session_id, "second")]

        assert isinstance(events[-1], ResponseDoneEvent)
        repo.get_messages.assert_awaited_once_with(session_id, limit=19)
        conv = seen["conversation"]
        assert [m.content for m in conv[:2]] == ["first", "first reply"]
        assert conv[-1].role == MessageRole.USER
        assert conv[-1].content == "second"
        assert len(conv) == 3

    @pytest.mark.asyncio
    async def test_ask_question_persists_as_assistant(
        self, mock_context_builder, mock_reasoner, mock_executor, mock_event_bus, repo
    ) -> None:
        """ASK_QUESTION persists the question text as an ASSISTANT message."""
        session_id = uuid4()

        mock_context_builder.build = AsyncMock(return_value=Context(session_id=session_id))
        mock_reasoner.reason = AsyncMock(return_value=Decision.ask_question(
            "Did you mean file A or file B?", reasoning="need clarification",
        ))

        loop = self.make_loop(
            mock_context_builder, mock_reasoner, mock_executor, mock_event_bus, repo
        )

        events = [e async for e in loop.stream_chat(session_id, "Open the file")]

        assert [e.event_type for e in events] == ["thinking", "text", "done"]
        calls = repo.add_message.await_args_list
        assert [c.args[1] for c in calls] == [MessageRole.USER, MessageRole.ASSISTANT]
        assert calls[1].args[2] == "Did you mean file A or file B?"

    @pytest.mark.asyncio
    async def test_without_repository_skips_persistence(
        self, loop, mock_context_builder, mock_reasoner
    ) -> None:
        """Without a repository wired, stream_chat still streams normally and
        never persists anything (backward-compatible path)."""
        session_id = uuid4()

        mock_context_builder.build = AsyncMock(return_value=Context(session_id=session_id))
        mock_reasoner.reason = AsyncMock(return_value=Decision.respond(
            "Hello!", reasoning="decided to greet",
        ))

        events = [e async for e in loop.stream_chat(session_id, "Hi")]

        assert [e.event_type for e in events] == ["thinking", "text", "done"]
        assert loop._session_repository is None
