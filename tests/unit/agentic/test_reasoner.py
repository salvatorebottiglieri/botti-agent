"""Tests for Reasoner."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from cortex.agentic.models import (
    AmbientContext,
    Context,
    Decision,
    DecisionType,
    MemoryContext,
    PersonalityContext,
)
from cortex.agentic.reasoner import Reasoner


class TestReasoner:
    """Tests for Reasoner."""

    @pytest.fixture
    def mock_llm_client(self):
        """Create a mock LLM client."""
        client = MagicMock()
        client.chat = AsyncMock()
        return client

    @pytest.fixture
    def mock_tool_registry(self):
        """Create a mock tool registry."""
        registry = MagicMock()
        return registry

    @pytest.fixture
    def reasoner(self, mock_llm_client, mock_tool_registry):
        """Create a Reasoner."""
        return Reasoner(
            llm_client=mock_llm_client,
            tool_registry=mock_tool_registry,
            system_prompt="You are a helpful assistant.",
        )

    @pytest.mark.asyncio
    async def test_reason_returns_decision(self, reasoner, mock_llm_client):
        """Reason should return a Decision."""
        mock_llm_client.chat = AsyncMock(return_value=MagicMock(
            content="Hello, how can I help?",
            tool_calls=[],
        ))

        context = Context(session_id=uuid4())

        decision = await reasoner.reason(context)

        assert decision is not None
        assert isinstance(decision, Decision)

    @pytest.mark.asyncio
    async def test_reason_calls_llm(self, reasoner, mock_llm_client):
        """Reason should call the LLM client."""
        mock_llm_client.chat = AsyncMock(return_value=MagicMock(
            content="Response",
            tool_calls=[],
        ))

        context = Context(session_id=uuid4())

        await reasoner.reason(context)

        mock_llm_client.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_reason_with_simple_response(self, reasoner, mock_llm_client):
        """Reason handles simple text responses."""
        mock_llm_client.chat = AsyncMock(return_value=MagicMock(
            content="The weather is sunny today.",
            tool_calls=[],
        ))

        context = Context(session_id=uuid4())

        decision = await reasoner.reason(context)

        assert decision.decision_type == DecisionType.RESPOND
        assert decision.text is not None

    @pytest.mark.asyncio
    async def test_reason_with_tool_calls(self, reasoner, mock_llm_client):
        """Reason handles tool call responses."""
        from cortex.tools.interfaces import ToolCall

        tool_call = ToolCall(id="call-1", name="file_read", arguments={"path": "/test"})

        mock_llm_client.chat = AsyncMock(return_value=MagicMock(
            content="Let me read that file.",
            tool_calls=[tool_call],
        ))

        context = Context(session_id=uuid4())

        decision = await reasoner.reason(context)

        assert decision.decision_type == DecisionType.EXECUTE_TOOLS
        assert decision.tool_calls is not None
        assert len(decision.tool_calls) == 1

    @pytest.mark.asyncio
    async def test_reason_includes_reasoning(self, reasoner, mock_llm_client):
        """Decision should include reasoning."""
        mock_llm_client.chat = AsyncMock(return_value=MagicMock(
            content="Done",
            tool_calls=[],
            reasoning="User asked a simple question, no tools needed",
        ))

        context = Context(session_id=uuid4())

        decision = await reasoner.reason(context)

        assert decision.reasoning is not None

    @pytest.mark.asyncio
    async def test_reason_uses_system_prompt(self, reasoner, mock_llm_client):
        """Reason should use the system prompt."""
        reasoner_with_custom = Reasoner(
            llm_client=mock_llm_client,
            tool_registry=MagicMock(),
            system_prompt="You are a pirate assistant. Arr!",
        )

        mock_llm_client.chat = AsyncMock(return_value=MagicMock(
            content="Arr!",
            tool_calls=[],
        ))

        context = Context(session_id=uuid4())

        await reasoner_with_custom.reason(context)

        # Check that chat was called with messages
        call_args = mock_llm_client.chat.call_args
        messages = call_args[0][0] if call_args[0] else call_args[1].get('messages', [])

        # System prompt should be in the messages
        system_messages = [m for m in messages if hasattr(m, 'role') and m.role == 'system']
        assert len(system_messages) > 0

    @pytest.mark.asyncio
    async def test_reason_with_empty_context(self, reasoner, mock_llm_client):
        """Reason handles empty context."""
        mock_llm_client.chat = AsyncMock(return_value=MagicMock(
            content="Hello!",
            tool_calls=[],
        ))

        context = Context(session_id=uuid4())

        decision = await reasoner.reason(context)

        assert decision is not None

    @pytest.mark.asyncio
    async def test_reason_passes_tools_via_structured_argument(self):
        """Tools are advertised via llm.chat(tools=...), not as system-prompt text."""
        from cortex.tools.interfaces import ToolDefinition

        mock_llm_client = MagicMock()
        mock_llm_client.chat = AsyncMock(return_value=MagicMock(
            content="Using a tool",
            tool_calls=[],
        ))

        reasoner = Reasoner(
            llm_client=mock_llm_client,
            tool_registry=MagicMock(),
        )

        tool_def = ToolDefinition(
            name="file_read",
            description="Read a file",
            input_schema={"type": "object", "properties": {}},
        )
        context = Context(session_id=uuid4(), tools=[tool_def])

        await reasoner.reason(context)

        call_kwargs = mock_llm_client.chat.call_args.kwargs
        assert call_kwargs.get("tools") is not None
        assert len(call_kwargs["tools"]) == 1
        assert call_kwargs["tools"][0].name == "file_read"
        assert call_kwargs["tools"][0].description == "Read a file"

    @pytest.mark.asyncio
    async def test_reason_omits_tools_kwarg_when_context_has_no_tools(self):
        """When context has no tools, llm.chat is called with tools=None."""
        mock_llm_client = MagicMock()
        mock_llm_client.chat = AsyncMock(return_value=MagicMock(
            content="ok",
            tool_calls=[],
        ))

        reasoner = Reasoner(
            llm_client=mock_llm_client,
            tool_registry=MagicMock(),
        )

        await reasoner.reason(Context(session_id=uuid4()))

        call_kwargs = mock_llm_client.chat.call_args.kwargs
        assert call_kwargs.get("tools") is None

    @pytest.mark.asyncio
    async def test_reason_passes_tool_calls_through_unchanged(self):
        """ToolCall is one type at both seams — no translation, just pass-through."""
        from cortex.tools.interfaces import ToolCall

        llm_call = ToolCall(id="call_1", name="file_read", arguments={"path": "/x"})
        mock_llm_client = MagicMock()
        mock_llm_client.chat = AsyncMock(return_value=MagicMock(
            content=None,
            tool_calls=[llm_call],
        ))

        reasoner = Reasoner(
            llm_client=mock_llm_client,
            tool_registry=MagicMock(),
        )

        decision = await reasoner.reason(Context(session_id=uuid4()))

        assert decision.tool_calls is not None
        assert len(decision.tool_calls) == 1
        assert decision.tool_calls[0] is llm_call
        assert decision.tool_calls[0].name == "file_read"
        assert decision.tool_calls[0].arguments == {"path": "/x"}


class TestReasonerEdgeCases:
    """Edge case tests for Reasoner."""

    @pytest.fixture
    def mock_llm_client(self):
        client = MagicMock()
        client.chat = AsyncMock()
        return client

    @pytest.mark.asyncio
    async def test_reason_handles_llm_error(self):
        """Reason handles LLM errors gracefully."""
        mock_llm_client = MagicMock()
        mock_llm_client.chat.side_effect = Exception("LLM error")

        reasoner = Reasoner(
            llm_client=mock_llm_client,
            tool_registry=MagicMock(),
        )

        context = Context(session_id=uuid4())

        # Should return an error decision, not raise
        decision = await reasoner.reason(context)

        # Error handling returns a respond decision with error message
        assert decision.decision_type == DecisionType.RESPOND
        assert "error" in decision.text.lower() or "try again" in decision.text.lower()

    @pytest.mark.asyncio
    async def test_reason_with_goal_mode(self, mock_llm_client):
        """Reason handles GOAL mode."""
        from cortex.agentic.models import GoalContext

        mock_llm_client.chat = AsyncMock(return_value=MagicMock(
            content="Working on the goal.",
            tool_calls=[],
        ))

        reasoner = Reasoner(
            llm_client=mock_llm_client,
            tool_registry=MagicMock(),
        )

        context = Context(
            session_id=uuid4(),
            goal=GoalContext(goal_id=uuid4(), description="Clean up files"),
        )

        decision = await reasoner.reason(context)

        assert decision is not None

    @pytest.mark.asyncio
    async def test_reason_with_personality_context(self, mock_llm_client):
        """Reason uses personality context for formatting."""
        mock_llm_client.chat = AsyncMock(return_value=MagicMock(
            content="As you wish.",
            tool_calls=[],
        ))

        reasoner = Reasoner(
            llm_client=mock_llm_client,
            tool_registry=MagicMock(),
        )

        context = Context(
            session_id=uuid4(),
            memory=MemoryContext(personality=PersonalityContext(formality=0.9)),
        )

        decision = await reasoner.reason(context)

        assert decision is not None

    @pytest.mark.asyncio
    async def test_reason_with_ambient_context(self, mock_llm_client):
        """Reason considers ambient context."""
        mock_llm_client.chat = AsyncMock(return_value=MagicMock(
            content="Good morning!",
            tool_calls=[],
        ))

        reasoner = Reasoner(
            llm_client=mock_llm_client,
            tool_registry=MagicMock(),
        )

        context = Context(
            session_id=uuid4(),
            memory=MemoryContext(ambient=AmbientContext(time_of_day="morning", location="home")),
        )

        decision = await reasoner.reason(context)

        assert decision is not None

    @pytest.mark.asyncio
    async def test_reason_multiple_tool_calls(self, mock_llm_client):
        """Reason handles multiple tool calls."""
        from cortex.tools.interfaces import ToolCall

        mock_llm_client.chat = AsyncMock(return_value=MagicMock(
            content="I'll read the file and then search.",
            tool_calls=[
                ToolCall(id="1", name="file_read", arguments={"path": "/test"}),
                ToolCall(id="2", name="grep", arguments={"pattern": "TODO"}),
            ],
        ))

        reasoner = Reasoner(
            llm_client=mock_llm_client,
            tool_registry=MagicMock(),
        )

        context = Context(session_id=uuid4())

        decision = await reasoner.reason(context)

        assert decision.decision_type == DecisionType.EXECUTE_TOOLS
        assert len(decision.tool_calls) == 2
