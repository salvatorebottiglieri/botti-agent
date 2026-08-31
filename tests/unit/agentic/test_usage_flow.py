"""Token usage and latency flow: LLM client -> reasoner -> loop -> events (issue #87)."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from cortex.agentic.events import ResponseDoneEvent
from cortex.agentic.loop import AgentLoop
from cortex.agentic.models import Context, Decision, DecisionType
from cortex.agentic.reasoner import Reasoner
from cortex.llm.models import ChatMessage, ChatResult, Role, UsageStats
from cortex.tools.interfaces import ToolCall, ToolResult


def _chat_result(
    content: str = "ok",
    tool_calls=None,
    usage: UsageStats | None = None,
) -> ChatResult:
    return ChatResult(
        message=ChatMessage(role=Role.ASSISTANT, content=content),
        tool_calls=tool_calls,
        usage=usage,
    )


class FakeClock:
    """Scripted clock for deterministic latency assertions."""

    def __init__(self, *readings: float):
        self._readings = list(readings)
        self._calls = 0

    def __call__(self) -> float:
        value = self._readings[self._calls]
        self._calls += 1
        return value


class TestReasonerThreadsUsage:
    """UsageStats from the LLM client reaches the Decision."""

    @pytest.fixture
    def client(self):
        return MagicMock()

    @pytest.fixture
    def reasoner(self, client):
        return Reasoner(llm_client=client, tool_registry=MagicMock())

    @pytest.mark.asyncio
    async def test_respond_decision_carries_usage(self, reasoner, client):
        client.chat = AsyncMock(return_value=_chat_result(
            content="Hello!",
            usage=UsageStats(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        ))
        decision = await reasoner.reason(Context(session_id=uuid4()))
        assert decision.decision_type == DecisionType.RESPOND
        assert decision.usage == UsageStats(
            prompt_tokens=100, completion_tokens=50, total_tokens=150
        )

    @pytest.mark.asyncio
    async def test_execute_tools_decision_carries_usage(self, reasoner, client):
        call = ToolCall(id="call_1", name="shell", arguments={})
        client.chat = AsyncMock(return_value=_chat_result(
            content=None,
            tool_calls=[call],
            usage=UsageStats(prompt_tokens=10, completion_tokens=2, total_tokens=12),
        ))
        decision = await reasoner.reason(Context(session_id=uuid4()))
        assert decision.decision_type == DecisionType.EXECUTE_TOOLS
        assert decision.usage == UsageStats(
            prompt_tokens=10, completion_tokens=2, total_tokens=12
        )

    @pytest.mark.asyncio
    async def test_missing_usage_yields_none(self, reasoner, client):
        client.chat = AsyncMock(return_value=_chat_result(content="ok", usage=None))
        decision = await reasoner.reason(Context(session_id=uuid4()))
        assert decision.usage is None

    @pytest.mark.asyncio
    async def test_error_path_has_no_usage(self, reasoner, client):
        client.chat = AsyncMock(side_effect=RuntimeError("boom"))
        decision = await reasoner.reason(Context(session_id=uuid4()))
        assert decision.usage is None


class TestLoopStreamsUsage:
    """The loop accumulates usage and measures latency into the event stream."""

    def _loop(self, clock=None):
        context_builder = MagicMock()
        context_builder.build = AsyncMock(return_value=Context(session_id=uuid4()))
        reasoner = MagicMock()
        reasoner.reason = AsyncMock()
        executor = MagicMock()
        executor.execute_single = AsyncMock()
        loop = AgentLoop(
            context_builder=context_builder,
            reasoner=reasoner,
            executor=executor,
            now=clock if clock is not None else (lambda: 0.0),
        )
        return loop, context_builder, reasoner, executor

    @pytest.mark.asyncio
    async def test_respond_done_event_carries_usage(self):
        loop, _cb, reasoner, _ex = self._loop()
        reasoner.reason = AsyncMock(return_value=Decision.respond(
            "Hello!",
            usage=UsageStats(prompt_tokens=7, completion_tokens=3, total_tokens=10),
        ))
        events = [e async for e in loop.stream_chat(uuid4(), "Hi")]
        done = events[-1]
        assert isinstance(done, ResponseDoneEvent)
        assert done.usage == UsageStats(
            prompt_tokens=7, completion_tokens=3, total_tokens=10
        )

    @pytest.mark.asyncio
    async def test_usage_accumulates_across_tool_iterations(self):
        loop, _cb, reasoner, executor = self._loop()
        call = ToolCall(id="call_1", name="shell", arguments={"cmd": "true"})
        reasoner.reason = AsyncMock(side_effect=[
            Decision.execute_tools(
                [call],
                usage=UsageStats(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            ),
            Decision.respond(
                "Done.",
                usage=UsageStats(prompt_tokens=100, completion_tokens=50, total_tokens=150),
            ),
        ])
        executor.execute_single = AsyncMock(return_value=ToolResult(
            tool_call_id="call_1", tool_name="shell", success=True, output="ok",
        ))
        events = [e async for e in loop.stream_chat(uuid4(), "Run")]
        done = events[-1]
        assert done.usage == UsageStats(
            prompt_tokens=110, completion_tokens=55, total_tokens=165
        )

    @pytest.mark.asyncio
    async def test_no_usage_anywhere_yields_none(self):
        loop, _cb, reasoner, _ex = self._loop()
        reasoner.reason = AsyncMock(return_value=Decision.respond("Hello!"))
        events = [e async for e in loop.stream_chat(uuid4(), "Hi")]
        assert events[-1].usage is None

    @pytest.mark.asyncio
    async def test_zero_usage_is_present_not_none(self):
        """A zero-usage response is still recorded — presence matters for eval accounting."""
        loop, _cb, reasoner, _ex = self._loop()
        reasoner.reason = AsyncMock(return_value=Decision.respond(
            "Hello!", usage=UsageStats(),
        ))
        events = [e async for e in loop.stream_chat(uuid4(), "Hi")]
        assert events[-1].usage == UsageStats()

    @pytest.mark.asyncio
    async def test_latency_ms_measured_from_stream_start_to_done(self):
        loop, _cb, reasoner, _ex = self._loop(clock=FakeClock(100.0, 100.5))
        reasoner.reason = AsyncMock(return_value=Decision.respond("Hello!"))
        events = [e async for e in loop.stream_chat(uuid4(), "Hi")]
        assert events[-1].latency_ms == pytest.approx(500.0)

    @pytest.mark.asyncio
    async def test_latency_ms_zero_when_instant(self):
        loop, _cb, reasoner, _ex = self._loop(clock=FakeClock(10.0, 10.0))
        reasoner.reason = AsyncMock(return_value=Decision.respond("Hello!"))
        events = [e async for e in loop.stream_chat(uuid4(), "Hi")]
        assert events[-1].latency_ms == 0.0


class TestRunChatCarriesUsage:
    """The drain wrapper forwards usage and latency into ChatResponse."""

    def _loop(self, clock=None):
        context_builder = MagicMock()
        context_builder.build = AsyncMock(return_value=Context(session_id=uuid4()))
        reasoner = MagicMock()
        reasoner.reason = AsyncMock()
        executor = MagicMock()
        loop = AgentLoop(
            context_builder=context_builder,
            reasoner=reasoner,
            executor=executor,
            now=clock if clock is not None else (lambda: 0.0),
        )
        return loop, reasoner

    @pytest.mark.asyncio
    async def test_run_chat_response_carries_usage_and_latency(self):
        loop, reasoner = self._loop(clock=FakeClock(0.0, 0.25))
        reasoner.reason = AsyncMock(return_value=Decision.respond(
            "Hello!",
            usage=UsageStats(prompt_tokens=7, completion_tokens=3, total_tokens=10),
        ))
        response = await loop.run_chat(uuid4(), "Hi")
        assert response.message == "Hello!"
        assert response.usage == UsageStats(
            prompt_tokens=7, completion_tokens=3, total_tokens=10
        )
        assert response.latency_ms == pytest.approx(250.0)

    @pytest.mark.asyncio
    async def test_run_chat_without_usage_yields_none(self):
        loop, reasoner = self._loop()
        reasoner.reason = AsyncMock(return_value=Decision.respond("Hello!"))
        response = await loop.run_chat(uuid4(), "Hi")
        assert response.usage is None
        assert response.latency_ms == 0.0

    @pytest.mark.asyncio
    async def test_stream_parity_usage_matches_done_event(self):
        loop, reasoner = self._loop()
        usage = UsageStats(prompt_tokens=20, completion_tokens=8, total_tokens=28)
        reasoner.reason = AsyncMock(return_value=Decision.respond("Done", usage=usage))
        response = await loop.run_chat(uuid4(), "Go")
        events = [e async for e in loop.stream_chat(uuid4(), "Go")]
        assert response.usage == events[-1].usage
        assert response.latency_ms == events[-1].latency_ms
