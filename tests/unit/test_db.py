"""Tests for the Database module."""

import pytest
from unittest.mock import patch

from cortex.db.session import DbSession
from cortex.db.pool import close_pool, get_pool


class TestDbSession:
    """Test cases for DbSession."""

    @pytest.mark.asyncio
    async def test_session_not_active_without_context(self):
        """Test that session methods raise when not in context."""
        session = DbSession()
        
        with pytest.raises(RuntimeError, match="not active"):
            await session.fetch("SELECT 1")

    @pytest.mark.asyncio
    async def test_execute_not_active(self):
        """Test that execute raises when not in context."""
        session = DbSession()
        
        with pytest.raises(RuntimeError, match="not active"):
            await session.execute("INSERT INTO test VALUES (1)")

    @pytest.mark.asyncio
    async def test_fetchrow_not_active(self):
        """Test that fetchrow raises when not in context."""
        session = DbSession()
        
        with pytest.raises(RuntimeError, match="not active"):
            await session.fetchrow("SELECT * FROM test")


class TestPoolManagement:
    """Test cases for pool management functions."""

    @pytest.mark.asyncio
    async def test_close_pool_when_none(self):
        """Test closing pool when none exists."""
        import cortex.db.pool as pool_module
        original = pool_module._pool
        pool_module._pool = None
        
        await close_pool()  # Should not raise
        
        # Restore
        pool_module._pool = original

    @pytest.mark.asyncio
    async def test_get_pool_not_initialized(self):
        """Test get_pool raises when not initialized."""
        import cortex.db.pool as pool_module
        original = pool_module._pool
        pool_module._pool = None
        
        with pytest.raises(RuntimeError, match="not initialized"):
            await get_pool()
        
        # Restore
        pool_module._pool = original
