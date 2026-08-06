"""Tests for ContextBuilder."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from cortex.agentic.context_builder import ContextBuilder
from cortex.agentic.models import (
    AmbientContext,
    MemoryContext,
    Mode,
    PersonalityContext,
)
from cortex.sessions.models import Message, MessageRole


class TestContextBuilder:
    """Tests for ContextBuilder."""

    @pytest.fixture
    def mock_session_repository(self):
        """Create a mock session repository."""
        repo = MagicMock()
        repo.get_messages = AsyncMock(return_value=[])
        return repo

    @pytest.fixture
    def mock_memory_service(self):
        """Create a mock memory service."""
        service = MagicMock()
        service.get_memory_context = AsyncMock(
            return_value=MemoryContext(personality=PersonalityContext())
        )
        return service

    @pytest.fixture
    def mock_tool_registry(self):
        """Create a mock tool registry."""
        registry = MagicMock()
        registry.get_schemas = MagicMock(return_value=[])
        return registry

    @pytest.fixture
    def builder(self, mock_session_repository, mock_memory_service, mock_tool_registry):
        """Create a ContextBuilder."""
        return ContextBuilder(
            session_repository=mock_session_repository,
            memory_service=mock_memory_service,
            tool_registry=mock_tool_registry,
        )

    @pytest.mark.asyncio
    async def test_build_returns_context(self, builder, mock_session_repository):
        """Build should return a Context object."""
        mock_session_repository.get_messages = AsyncMock(return_value=[
            Message(session_id=uuid4(), role=MessageRole.USER, content="Hi")
        ])

        context = await builder.build(
            session_id=uuid4(),
            user_message="Hello",
            mode=Mode.CHAT,
        )

        assert context is not None
        assert context.session_id is not None

    @pytest.mark.asyncio
    async def test_build_includes_conversation(self, builder, mock_session_repository):
        """Build should include conversation history."""
        messages = [
            Message(session_id=uuid4(), role=MessageRole.USER, content="Hello"),
            Message(session_id=uuid4(), role=MessageRole.ASSISTANT, content="Hi there!"),
        ]
        mock_session_repository.get_messages = AsyncMock(return_value=messages)

        context = await builder.build(
            session_id=uuid4(),
            user_message="How are you?",
            mode=Mode.CHAT,
        )

        # Should include history and the new message
        assert len(context.conversation) >= 2

    @pytest.mark.asyncio
    async def test_build_includes_tools(self, builder, mock_tool_registry):
        """Build should include available tools."""
        tools = [{"name": "file_read", "description": "Read a file"}]
        mock_tool_registry.get_schemas = MagicMock(return_value=tools)

        context = await builder.build(
            session_id=uuid4(),
            user_message="Read my file",
            mode=Mode.CHAT,
        )

        assert len(context.tools) == 1

    @pytest.mark.asyncio
    async def test_build_includes_personality(self, builder, mock_memory_service):
        """Build should include personality context from the Memory bundle."""
        mock_memory_service.get_memory_context = AsyncMock(
            return_value=MemoryContext(personality=PersonalityContext(formality=0.8))
        )

        context = await builder.build(
            session_id=uuid4(),
            user_message="Hello",
            mode=Mode.CHAT,
        )

        assert context.memory.personality is not None
        assert context.memory.personality.formality == 0.8

    @pytest.mark.asyncio
    async def test_build_includes_ambient_context(self, builder, mock_memory_service):
        """Build should include ambient context from the Memory bundle."""
        mock_memory_service.get_memory_context = AsyncMock(
            return_value=MemoryContext(
                ambient=AmbientContext(time_of_day="afternoon", location="home"),
            )
        )

        context = await builder.build(
            session_id=uuid4(),
            user_message="What's the weather?",
            mode=Mode.CHAT,
        )

        assert context.memory.ambient is not None
        assert context.memory.ambient.time_of_day == "afternoon"

    @pytest.mark.asyncio
    async def test_build_with_goal_mode(self, builder, mock_memory_service):
        """Build should include goal context in GOAL mode."""
        goal_id = uuid4()

        context = await builder.build(
            session_id=uuid4(),
            user_message="Clean up files",
            mode=Mode.GOAL,
            goal_id=goal_id,
        )

        assert context.goal is not None
        assert context.goal.goal_id == goal_id

    @pytest.mark.asyncio
    async def test_build_includes_relevant_facts(self, builder, mock_memory_service):
        """Build should include relevant facts from the Memory bundle."""
        from cortex.memory.models import Fact, FactMutability, FactType

        facts = [
            Fact(
                type=FactType.LOCATION,
                symbolic_repr="location.home",
                natural_lang_repr="At home",
                mutability=FactMutability.MUTABLE,
            )
        ]
        mock_memory_service.get_memory_context = AsyncMock(
            return_value=MemoryContext(facts=facts)
        )

        context = await builder.build(
            session_id=uuid4(),
            user_message="Where am I?",
            mode=Mode.CHAT,
        )

        assert len(context.memory.facts) == 1
        assert context.memory.facts[0].type == FactType.LOCATION

    @pytest.mark.asyncio
    async def test_build_respects_message_limit(self, builder, mock_session_repository):
        """Build should limit conversation history."""
        # Create many messages
        many_messages = [
            Message(session_id=uuid4(), role=MessageRole.USER, content=f"Message {i}")
            for i in range(100)
        ]

        # Mock respects the limit parameter
        async def mock_get_messages(session_id, limit=50):
            return many_messages[:limit]

        mock_session_repository.get_messages = mock_get_messages

        context = await builder.build(
            session_id=uuid4(),
            user_message="Latest message",
            mode=Mode.CHAT,
        )

        # Should be limited to max_messages (20) including the new user message
        assert len(context.conversation) <= builder.max_messages

    @pytest.mark.asyncio
    async def test_build_uses_fact_type_filter(self, builder, mock_memory_service):
        """Build should forward fact_types to the Memory bundle call."""
        from cortex.memory.models import FactType

        await builder.build(
            session_id=uuid4(),
            user_message="Test",
            mode=Mode.CHAT,
            fact_types=["location"],
        )

        mock_memory_service.get_memory_context.assert_called_once()
        call_kwargs = mock_memory_service.get_memory_context.call_args.kwargs
        assert call_kwargs["fact_types"] == [FactType.LOCATION]

    @pytest.mark.asyncio
    async def test_build_with_empty_session(self, builder, mock_session_repository):
        """Build handles session with no messages."""
        mock_session_repository.get_messages = AsyncMock(return_value=[])

        context = await builder.build(
            session_id=uuid4(),
            user_message="First message",
            mode=Mode.CHAT,
        )

        # Should have the user's message (no previous history)
        assert len(context.conversation) == 1
        assert context.conversation[0].content == "First message"

    @pytest.mark.asyncio
    async def test_build_adds_user_message_to_conversation(self, builder):
        """Build should append the user's message to conversation."""
        context = await builder.build(
            session_id=uuid4(),
            user_message="Tell me a story",
            mode=Mode.CHAT,
        )

        # The last message should be the user's
        if context.conversation:
            last_msg = context.conversation[-1]
            assert last_msg.content == "Tell me a story"


class TestContextBuilderEdgeCases:
    """Edge case tests for ContextBuilder."""

    @pytest.fixture
    def mock_session_repository(self):
        service = MagicMock()
        service.get_messages = AsyncMock(return_value=[])
        return service

    @pytest.fixture
    def mock_memory_service(self):
        service = MagicMock()
        service.get_memory_context = AsyncMock(return_value=MemoryContext())
        return service

    @pytest.fixture
    def mock_tool_registry(self):
        return MagicMock(spec=[])  # Empty spec

    @pytest.mark.asyncio
    async def test_build_handles_missing_personality(self, mock_session_repository, mock_memory_service, mock_tool_registry):
        """Build handles when personality is None."""
        builder = ContextBuilder(
            session_repository=mock_session_repository,
            memory_service=mock_memory_service,
            tool_registry=mock_tool_registry,
        )

        context = await builder.build(
            session_id=uuid4(),
            user_message="Hi",
            mode=Mode.CHAT,
        )

        # Should still work with None personality
        assert context.memory.personality is None or isinstance(context.memory.personality, PersonalityContext)

    @pytest.mark.asyncio
    async def test_build_handles_tool_registry_exception(self, mock_session_repository, mock_memory_service):
        """Build handles when tool registry fails."""
        mock_tool_registry = MagicMock()
        mock_tool_registry.get_schemas.side_effect = Exception("Registry error")

        builder = ContextBuilder(
            session_repository=mock_session_repository,
            memory_service=mock_memory_service,
            tool_registry=mock_tool_registry,
        )

        # Should not raise, should return empty tools
        context = await builder.build(
            session_id=uuid4(),
            user_message="Hi",
            mode=Mode.CHAT,
        )

        assert context.tools == []

    @pytest.mark.asyncio
    async def test_build_handles_degraded_memory(self, mock_session_repository):
        """Build returns a Context when the Memory bundle reports degraded dimensions."""
        mock_memory_service = MagicMock()
        mock_memory_service.get_memory_context = AsyncMock(
            return_value=MemoryContext(
                degraded_dimensions=["facts", "personality", "ambient"],
            )
        )

        builder = ContextBuilder(
            session_repository=mock_session_repository,
            memory_service=mock_memory_service,
            tool_registry=MagicMock(),
        )

        context = await builder.build(
            session_id=uuid4(),
            user_message="Hi",
            mode=Mode.CHAT,
        )

        assert context is not None
        assert context.memory.facts == []
        assert context.memory.personality is None
        assert context.memory.ambient is None
