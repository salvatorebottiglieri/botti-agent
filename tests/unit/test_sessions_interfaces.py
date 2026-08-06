"""Tests for session repository interface."""

from abc import ABC

import pytest

from cortex.sessions.interfaces import SessionRepository


class TestSessionRepositoryIsAbstract:
    """Verify the repository interface is properly abstract."""

    def test_repository_is_abc(self):
        """Test that SessionRepository is an ABC."""
        assert issubclass(SessionRepository, ABC)

    def test_repository_has_required_methods(self):
        """Test that all required methods are defined."""
        methods = [
            'create',
            'get',
            'update_state',
            'update_activity',
            'add_message',
            'get_messages',
            'list_active',
        ]

        for method in methods:
            assert hasattr(SessionRepository, method)
            assert callable(getattr(SessionRepository, method))

    def test_cannot_instantiate_directly(self):
        """Test that we cannot create a repository directly."""
        with pytest.raises(TypeError, match="abstract"):
            SessionRepository()
