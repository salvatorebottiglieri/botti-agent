"""Wiring tests: ExecutionModule extracts user facts before delegating to the loop.

The conversation extractor is injected at the composition root. Both chat
entry points must call it with the user message and session id *before* the
agent loop sees the turn, and extraction must never break the chat path.
"""

from uuid import UUID, uuid4

import pytest

from cortex.agentic.events import TextDeltaEvent
from cortex.agentic.models import ChatResponse
from cortex.execution.module import ExecutionModule


class RecordingExtractor:
    """ConversationFactExtractor stub that records call order and arguments."""

    def __init__(self, order: list[str], *, exc: Exception | None = None) -> None:
        self.order = order
        self.exc = exc
        self.calls: list[tuple[str, UUID | None]] = []

    async def extract_and_store(self, text: str, *, session_id: UUID | None = None):
        self.order.append("extract")
        self.calls.append((text, session_id))
        if self.exc is not None:
            raise self.exc
        return []


class RecordingLoop:
    """Agent loop stub that records when it is entered."""

    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.calls: list[tuple[UUID, str]] = []

    async def run_chat(
        self,
        session_id: UUID,
        user_message: str,
        *,
        max_iterations: int | None = None,
    ) -> ChatResponse:
        self.order.append("loop.run_chat")
        self.calls.append((session_id, user_message))
        return ChatResponse(message="ok", iterations=0)


class StreamingLoop:
    """Async-generator loop stub recording when streaming starts."""

    def __init__(self, order: list[str], events) -> None:
        self.order = order
        self.events = events
        self.calls: list[tuple[UUID, str]] = []

    async def stream_chat(
        self,
        session_id: UUID,
        user_message: str,
        *,
        max_iterations: int | None = None,
        stream: bool = False,
    ):
        self.order.append("loop.stream_chat")
        self.calls.append((session_id, user_message))
        for event in self.events:
            yield event


class TestRunChatExtraction:
    """run_chat extracts before the agent loop runs."""

    @pytest.mark.asyncio
    async def test_run_chat_extracts_before_delegating(self):
        """Extraction is awaited with the user message before loop.run_chat."""
        order: list[str] = []
        extractor = RecordingExtractor(order)
        loop = RecordingLoop(order)
        module = ExecutionModule(agent_loop=loop, conversation_extractor=extractor)
        session_id = uuid4()

        response = await module.run_chat(session_id, "I was born in Rome")

        assert response.message == "ok"
        assert order == ["extract", "loop.run_chat"]
        assert extractor.calls == [("I was born in Rome", session_id)]
        assert loop.calls == [(session_id, "I was born in Rome")]

    @pytest.mark.asyncio
    async def test_run_chat_survives_an_extraction_failure(self):
        """An extractor exception is logged, not propagated into chat."""
        order: list[str] = []
        extractor = RecordingExtractor(order, exc=RuntimeError("memory down"))
        loop = RecordingLoop(order)
        module = ExecutionModule(agent_loop=loop, conversation_extractor=extractor)

        response = await module.run_chat(uuid4(), "hello")

        assert response.message == "ok"
        assert order == ["extract", "loop.run_chat"]


class TestStreamChatExtraction:
    """stream_chat extracts before the first event is produced."""

    @pytest.mark.asyncio
    async def test_stream_chat_extracts_before_the_stream_starts(self):
        """Extraction happens before the loop's async generator is first advanced."""
        order: list[str] = []
        session_id = uuid4()
        extractor = RecordingExtractor(order)
        loop = StreamingLoop(order, [TextDeltaEvent(session_id, delta="hi")])
        module = ExecutionModule(agent_loop=loop, conversation_extractor=extractor)

        seen = [event async for event in module.stream_chat(session_id, "hello")]

        assert [event.delta for event in seen] == ["hi"]
        assert order == ["extract", "loop.stream_chat"]
        assert extractor.calls == [("hello", session_id)]
        assert loop.calls == [(session_id, "hello")]

    @pytest.mark.asyncio
    async def test_stream_chat_survives_an_extraction_failure(self):
        """An extractor exception never breaks or alters the event stream."""
        order: list[str] = []
        session_id = uuid4()
        extractor = RecordingExtractor(order, exc=RuntimeError("memory down"))
        loop = StreamingLoop(order, [TextDeltaEvent(session_id, delta="hi")])
        module = ExecutionModule(agent_loop=loop, conversation_extractor=extractor)

        seen = [event async for event in module.stream_chat(session_id, "hello")]

        assert [event.delta for event in seen] == ["hi"]
        assert order == ["extract", "loop.stream_chat"]
