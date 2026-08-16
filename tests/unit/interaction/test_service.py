"""Tests for InteractionModule."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from cortex.interaction.service import InteractionService


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
    def service(self, mock_execution_module, mock_session_repository):
        return InteractionService(
            execution_module=mock_execution_module,
            session_repository=mock_session_repository,
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
