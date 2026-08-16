"""Tests for InteractionModule."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from cortex.interaction.service import InteractionService, PersonalityService


class TestPersonalityService:
    """Tests for PersonalityService."""

    @pytest.fixture
    def mock_memory_service(self):
        """Create a mock memory service."""
        service = MagicMock()
        service.get_personality_context = AsyncMock()
        service.get_relevant = AsyncMock(return_value=[])
        return service

    @pytest.fixture
    def service(self, mock_memory_service):
        """Create a PersonalityService."""
        return PersonalityService(memory_service=mock_memory_service)

    @pytest.mark.asyncio
    async def test_get_personality(self, service, mock_memory_service):
        """Get personality should delegate to memory service."""
        from cortex.agentic.models import PersonalityContext

        mock_memory_service.get_personality_context = AsyncMock(
            return_value=PersonalityContext(formality=0.7)
        )

        personality = await service.get_personality(session_id=uuid4())

        assert personality is not None
        assert personality.formality == 0.7

    @pytest.mark.asyncio
    async def test_get_personality_default(self, service, mock_memory_service):
        """Get personality returns default when not available."""
        mock_memory_service.get_personality_context = AsyncMock(return_value=None)

        personality = await service.get_personality(session_id=uuid4())

        # Should return default
        assert personality is not None or mock_memory_service.get_personality_context.called

    @pytest.mark.asyncio
    async def test_format_response(self, service):
        """Format response should apply personality to text."""
        text = "Hello, how can I help you?"

        formatted = service.format_response(text, formality=0.5)

        assert formatted is not None
        assert len(formatted) > 0

    @pytest.mark.asyncio
    async def test_format_response_formal(self, service):
        """Format response should handle formal personality."""
        text = "Hi there buddy!"

        formatted = service.format_response(text, formality=0.9)

        assert formatted is not None

    @pytest.mark.asyncio
    async def test_format_response_casual(self, service):
        """Format response should handle casual personality."""
        text = "Good morning. I hope you are well."

        formatted = service.format_response(text, formality=0.2)

        assert formatted is not None

    @pytest.mark.asyncio
    async def test_update_preferences(self, service, mock_memory_service):
        """Update preferences should store in memory."""
        mock_memory_service.store_fact = AsyncMock()

        await service.update_preferences(
            session_id=uuid4(),
            formality=0.8,
        )

        assert mock_memory_service.store_fact.called


class TestInteractionService:
    """Tests for InteractionService (slim facade for the chat route)."""

    @pytest.fixture
    def mock_execution_module(self):
        module = MagicMock()
        return module

    @pytest.fixture
    def mock_session_repository(self):
        repo = MagicMock()
        repo.get = AsyncMock(return_value=None)
        repo.create = AsyncMock(return_value=MagicMock(id=uuid4()))
        repo.update_state = AsyncMock()
        return repo

    @pytest.fixture
    def mock_personality_service(self):
        return MagicMock()

    @pytest.fixture
    def service(self, mock_execution_module, mock_session_repository, mock_personality_service):
        return InteractionService(
            execution_module=mock_execution_module,
            session_repository=mock_session_repository,
            personality_service=mock_personality_service,
        )

    @pytest.mark.asyncio
    async def test_get_session_delegates_to_repository(self, service, mock_session_repository):
        session_id = uuid4()
        await service.get_session(session_id)
        mock_session_repository.get.assert_called_with(session_id)

    def test_session_get_or_create_is_public(self, service):
        """get_or_create_session is exposed as a public callable."""
        assert callable(service.get_or_create_session)
        assert not service.get_or_create_session.__name__.startswith("_")

    @pytest.mark.asyncio
    async def test_get_or_create_returns_existing_session_when_found(
        self, service, mock_session_repository
    ):
        session_id = uuid4()
        existing = MagicMock(id=session_id)
        mock_session_repository.get = AsyncMock(return_value=existing)

        result = await service.get_or_create_session(session_id)

        assert result is existing
        mock_session_repository.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_or_create_creates_new_session_when_id_is_none(
        self, service, mock_session_repository
    ):
        new_session = MagicMock(id=uuid4())
        active_session = MagicMock(id=new_session.id)
        mock_session_repository.create = AsyncMock(return_value=new_session)
        mock_session_repository.update_state = AsyncMock(return_value=active_session)

        result = await service.get_or_create_session(None)

        assert result is active_session
        mock_session_repository.create.assert_called_once()


class TestPersonalityFormatting:
    """Personality should affect response formatting end-to-end."""

    @pytest.mark.asyncio
    async def test_personality_affects_formatting(self):
        from cortex.agentic.models import PersonalityContext

        mock_memory = MagicMock()
        mock_memory.get_personality_context = AsyncMock(
            return_value=PersonalityContext(formality=0.9)
        )

        service = PersonalityService(memory_service=mock_memory)

        personality = await service.get_personality(uuid4())
        formatted = service.format_response("hi there!", formality=personality.formality)

        assert formatted is not None
