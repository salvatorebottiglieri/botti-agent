"""Tests for Reasoner."""

import inspect
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
from cortex.llm.models import ChatMessage, ChatResult, Role


def _chat_result(content: str | None = None, tool_calls=None) -> ChatResult:
    """Build a ChatResult with an assistant message, as llm.chat would return."""
    return ChatResult(
        message=ChatMessage(role=Role.ASSISTANT, content=content),
        tool_calls=tool_calls,
    )


def _chat_stream(*items):
    """Return a callable mimicking llm.chat_stream: an async generator over
    `items` (str deltas, then a terminal ChatResult)."""
    async def gen(messages, *, tools=None, generation_config=None):
        for it in items:
            yield it
    return gen


async def _drain_reason_stream(agen):
    """Split reason_stream output into (text deltas, terminal Decision)."""
    deltas: list[str] = []
    decision = None
    async for item in agen:
        if isinstance(item, Decision):
            decision = item
        else:
            deltas.append(item)
    return deltas, decision


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
        mock_llm_client.chat = AsyncMock(return_value=_chat_result(content="Hello, how can I help?", tool_calls=[]))

        context = Context(session_id=uuid4())

        decision = await reasoner.reason(context)

        assert decision is not None
        assert isinstance(decision, Decision)

    @pytest.mark.asyncio
    async def test_reason_calls_llm(self, reasoner, mock_llm_client):
        """Reason should call the LLM client."""
        mock_llm_client.chat = AsyncMock(return_value=_chat_result(content="Response", tool_calls=[]))

        context = Context(session_id=uuid4())

        await reasoner.reason(context)

        mock_llm_client.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_reason_with_simple_response(self, reasoner, mock_llm_client):
        """Reason handles simple text responses."""
        mock_llm_client.chat = AsyncMock(return_value=_chat_result(content="The weather is sunny today.", tool_calls=[]))

        context = Context(session_id=uuid4())

        decision = await reasoner.reason(context)

        assert decision.decision_type == DecisionType.RESPOND
        assert decision.text is not None

    @pytest.mark.asyncio
    async def test_reason_with_tool_calls(self, reasoner, mock_llm_client):
        """Reason handles tool call responses."""
        from cortex.tools.interfaces import ToolCall

        tool_call = ToolCall(id="call-1", name="file_read", arguments={"path": "/test"})

        mock_llm_client.chat = AsyncMock(return_value=_chat_result(content="Let me read that file.", tool_calls=[tool_call]))

        context = Context(session_id=uuid4())

        decision = await reasoner.reason(context)

        assert decision.decision_type == DecisionType.EXECUTE_TOOLS
        assert decision.tool_calls is not None
        assert len(decision.tool_calls) == 1

    @pytest.mark.asyncio
    async def test_reason_includes_reasoning(self, reasoner, mock_llm_client):
        """Decision should include reasoning."""
        mock_llm_client.chat = AsyncMock(return_value=_chat_result(content="Done"))

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

        mock_llm_client.chat = AsyncMock(return_value=_chat_result(content="Arr!", tool_calls=[]))

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
        mock_llm_client.chat = AsyncMock(return_value=_chat_result(content="Hello!", tool_calls=[]))

        context = Context(session_id=uuid4())

        decision = await reasoner.reason(context)

        assert decision is not None

    @pytest.mark.asyncio
    async def test_reason_passes_tools_via_structured_argument(self):
        """Tools are advertised via llm.chat(tools=...), not as system-prompt text."""
        from cortex.tools.interfaces import ToolDefinition

        mock_llm_client = MagicMock()
        mock_llm_client.chat = AsyncMock(return_value=_chat_result(content="Using a tool", tool_calls=[]))

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
        mock_llm_client.chat = AsyncMock(return_value=_chat_result(content="ok", tool_calls=[]))

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
        mock_llm_client.chat = AsyncMock(return_value=_chat_result(content=None, tool_calls=[llm_call]))

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


    @pytest.mark.asyncio
    async def test_reason_preserves_tool_call_ids_in_prompt(self, reasoner, mock_llm_client):
        """Tool messages carry tool_call_id and assistant tool_calls (as
        ToolCall objects) into the LLM prompt — provider-agnostic, no wire shape."""
        from cortex.sessions.models import Message, MessageRole

        mock_llm_client.chat = AsyncMock(return_value=_chat_result(content="ok", tool_calls=[]))
        session_id = uuid4()
        context = Context(session_id=session_id, conversation=[
            Message(
                session_id=session_id,
                role=MessageRole.ASSISTANT,
                content="",
                tool_calls=[{
                    "id": "call_x",
                    "name": "shell",
                    "arguments": {"command": "echo hi"},
                }],
            ),
            Message(
                session_id=session_id,
                role=MessageRole.TOOL_RESULT,
                content="output",
                tool_call_id="call_x",
            ),
        ])

        await reasoner.reason(context)

        call_args = mock_llm_client.chat.call_args
        messages = call_args[0][0] if call_args[0] else call_args[1]["messages"]
        tool_msg = next(m for m in messages if m.role.value == "tool")
        assistant_msg = next(
            m for m in messages if m.role.value == "assistant" and m.tool_calls
        )

        assert tool_msg.tool_call_id == "call_x"
        assert len(assistant_msg.tool_calls) == 1
        assert assistant_msg.tool_calls[0].id == "call_x"
        assert assistant_msg.tool_calls[0].name == "shell"
        assert assistant_msg.tool_calls[0].arguments == {"command": "echo hi"}


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

        mock_llm_client.chat = AsyncMock(return_value=_chat_result(content="Working on the goal.", tool_calls=[]))

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
        mock_llm_client.chat = AsyncMock(return_value=_chat_result(content="As you wish.", tool_calls=[]))

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
        mock_llm_client.chat = AsyncMock(return_value=_chat_result(content="Good morning!", tool_calls=[]))

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

        mock_llm_client.chat = AsyncMock(return_value=_chat_result(content="I'll read the file and then search.", tool_calls=[
            ToolCall(id="1", name="file_read", arguments={"path": "/test"}),
            ToolCall(id="2", name="grep", arguments={"pattern": "TODO"}),
        ]))

        reasoner = Reasoner(
            llm_client=mock_llm_client,
            tool_registry=MagicMock(),
        )

        context = Context(session_id=uuid4())

        decision = await reasoner.reason(context)

        assert decision.decision_type == DecisionType.EXECUTE_TOOLS
        assert len(decision.tool_calls) == 2


class TestReasonerStream:
    """Tests for Reasoner.reason_stream (streaming variant of reason)."""

    @pytest.fixture
    def mock_llm_client(self):
        client = MagicMock()
        client.chat_stream = MagicMock()
        return client

    @pytest.fixture
    def reasoner(self, mock_llm_client):
        return Reasoner(
            llm_client=mock_llm_client,
            tool_registry=MagicMock(),
            system_prompt="You are a helpful assistant.",
        )

    @pytest.mark.asyncio
    async def test_yields_deltas_then_respond_decision(self, reasoner, mock_llm_client):
        """Text deltas are yielded in order, then a RESPOND Decision built from
        the assembled ChatResult."""
        mock_llm_client.chat_stream = _chat_stream(
            "Hello", " world", _chat_result(content="Hello world", tool_calls=[]),
        )

        deltas, decision = await _drain_reason_stream(
            reasoner.reason_stream(Context(session_id=uuid4()))
        )

        assert deltas == ["Hello", " world"]
        assert decision is not None
        assert decision.decision_type == DecisionType.RESPOND
        assert decision.text == "Hello world"

    @pytest.mark.asyncio
    async def test_tool_call_turn_yields_no_deltas(self, reasoner, mock_llm_client):
        """A tool-call turn carries no assistant text: no str deltas, only the
        final EXECUTE_TOOLS Decision."""
        from cortex.tools.interfaces import ToolCall

        tc = ToolCall(id="call_1", name="file_read", arguments={"path": "/x"})
        mock_llm_client.chat_stream = _chat_stream(
            _chat_result(content=None, tool_calls=[tc]),
        )

        deltas, decision = await _drain_reason_stream(
            reasoner.reason_stream(Context(session_id=uuid4()))
        )

        assert deltas == []
        assert decision.decision_type == DecisionType.EXECUTE_TOOLS
        assert decision.tool_calls[0] is tc

    @pytest.mark.asyncio
    async def test_streamed_text_with_marker_like_content_is_plain_respond(
        self, reasoner, mock_llm_client
    ):
        """[QUESTION] markers are obsolete: text that happens to contain them
        streams verbatim and yields a plain RESPOND (clarification is a tool)."""
        mock_llm_client.chat_stream = _chat_stream(
            "[QUESTION]", "Quale budget?", "[/QUESTION]",
            _chat_result(content="[QUESTION]Quale budget?[/QUESTION]", tool_calls=[]),
        )

        deltas, decision = await _drain_reason_stream(
            reasoner.reason_stream(Context(session_id=uuid4()))
        )

        assert "".join(deltas) == "[QUESTION]Quale budget?[/QUESTION]"
        assert decision.decision_type == DecisionType.RESPOND
        assert decision.text == "[QUESTION]Quale budget?[/QUESTION]"

    @pytest.mark.asyncio
    async def test_stream_without_final_result_yields_error_decision(
        self, reasoner, mock_llm_client
    ):
        """If the stream ends without a terminal ChatResult, reason_stream does
        not raise — it yields a fallback RESPOND Decision after the deltas."""
        mock_llm_client.chat_stream = _chat_stream("partial")  # no ChatResult

        deltas, decision = await _drain_reason_stream(
            reasoner.reason_stream(Context(session_id=uuid4()))
        )

        assert deltas == ["partial"]
        assert decision.decision_type == DecisionType.RESPOND
        assert "try again" in decision.text.lower()

    @pytest.mark.asyncio
    async def test_stream_error_yields_error_decision(self, reasoner, mock_llm_client):
        """An exception raised while streaming is caught and surfaced as a
        fallback RESPOND Decision, mirroring reason()'s error handling."""
        async def boom(messages, *, tools=None, generation_config=None):
            raise RuntimeError("stream exploded")
            yield  # pragma: no cover - marks this a generator

        mock_llm_client.chat_stream = boom

        deltas, decision = await _drain_reason_stream(
            reasoner.reason_stream(Context(session_id=uuid4()))
        )

        assert deltas == []
        assert decision.decision_type == DecisionType.RESPOND
        assert "try again" in decision.text.lower()

    @pytest.mark.asyncio
    async def test_stream_passes_tools_via_structured_argument(self, reasoner, mock_llm_client):
        """Tools are advertised via llm.chat_stream(tools=...), same as reason()."""
        from cortex.tools.interfaces import ToolDefinition

        mock_llm_client.chat_stream = MagicMock(
            side_effect=_chat_stream(_chat_result(content="ok", tool_calls=[]))
        )
        tool_def = ToolDefinition(
            name="file_read",
            description="Read a file",
            input_schema={"type": "object", "properties": {}},
        )
        context = Context(session_id=uuid4(), tools=[tool_def])

        await _drain_reason_stream(reasoner.reason_stream(context))

        call_kwargs = mock_llm_client.chat_stream.call_args.kwargs
        assert call_kwargs.get("tools") is not None
        assert len(call_kwargs["tools"]) == 1
        assert call_kwargs["tools"][0].name == "file_read"


class TestReasonerParseResponse:
    """Tests for Reasoner._parse_response parsing rules (issues #24, #25)."""

    @pytest.fixture
    def mock_llm_client(self):
        client = MagicMock()
        client.chat = AsyncMock()
        return client

    @pytest.fixture
    def mock_tool_registry(self):
        registry = MagicMock()
        return registry

    @pytest.fixture
    def reasoner(self, mock_llm_client, mock_tool_registry):
        return Reasoner(
            llm_client=mock_llm_client,
            tool_registry=mock_tool_registry,
            system_prompt="You are a helpful assistant.",
        )

    @pytest.fixture
    def context(self):
        return Context(session_id=uuid4())

    def test_empty_content_raises_value_error(self, reasoner, context):
        """Empty response (no content, no tool calls) raises ValueError (#24)."""
        result = _chat_result(content=None, tool_calls=[])

        with pytest.raises(ValueError, match="Empty response from LLM"):
            reasoner._parse_response(result, context)

    def test_missing_message_raises_value_error(self, reasoner, context):
        """A ChatResult without a message raises ValueError (#24)."""
        result = ChatResult.model_construct(message=None)

        with pytest.raises(ValueError, match="Empty response from LLM"):
            reasoner._parse_response(result, context)

    def test_parse_response_has_no_getattr(self):
        """Typed field access only — no getattr chains in _parse_response (#24)."""
        source = inspect.getsource(Reasoner._parse_response)
        assert "getattr(" not in source

    def test_tool_calls_take_priority_over_content(self, reasoner, context):
        """Tool calls win over any assistant content."""
        from cortex.tools.interfaces import ToolCall

        tool_call = ToolCall(id="call-1", name="file_read", arguments={"path": "/x"})
        result = _chat_result(content="Some text", tool_calls=[tool_call])

        decision = reasoner._parse_response(result, context)

        assert decision.decision_type == DecisionType.EXECUTE_TOOLS

    def test_question_markers_are_no_longer_parsed(self, reasoner, context):
        """[QUESTION] markers are obsolete: text that contains them is a plain
        RESPOND now (clarification goes through the ask_user tool)."""
        result = _chat_result("[QUESTION]Quale budget?[/QUESTION]")

        decision = reasoner._parse_response(result, context)

        assert decision.decision_type == DecisionType.RESPOND
        assert decision.text == "[QUESTION]Quale budget?[/QUESTION]"

    def test_default_system_prompt_points_to_ask_user_tool(self):
        """The default prompt steers clarification to the ask_user tool and no
        longer teaches the old [QUESTION] marker convention."""
        reasoner = Reasoner(
            llm_client=MagicMock(),
            tool_registry=MagicMock(),
        )

        assert "ask_user" in reasoner._system_prompt
        assert "[QUESTION]" not in reasoner._system_prompt
