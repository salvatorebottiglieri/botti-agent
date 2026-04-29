"""Async database session context manager."""

import logging
from typing import Any

import asyncpg

from cortex.db.pool import get_pool

logger = logging.getLogger(__name__)


class DbSession:
    """
    Async context manager for database access.
    
    Provides a simple interface for executing queries
    against the shared connection pool.
    
    Example:
        async with DbSession() as session:
            rows = await session.fetch("SELECT * FROM sessions WHERE id = $1", session_id)
            if rows:
                return dict(rows[0])
    """

    def __init__(self):
        self._conn: asyncpg.Connection | None = None

    async def __aenter__(self) -> "DbSession":
        """Acquire a connection from the pool."""
        pool = await get_pool()
        self._conn = await pool.acquire()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Release the connection back to the pool."""
        if self._conn is not None:
            pool = await get_pool()
            await pool.release(self._conn)
            self._conn = None

    async def fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        """
        Execute a query and return all rows.
        
        Args:
            query: SQL query with $1, $2 placeholders
            *args: Query parameters
            
        Returns:
            List of asyncpg.Record objects
        """
        if self._conn is None:
            raise RuntimeError("Session not active. Use 'async with DbSession()'")
        
        return await self._conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        """
        Execute a query and return a single row.
        
        Args:
            query: SQL query with $1, $2 placeholders
            *args: Query parameters
            
        Returns:
            asyncpg.Record or None if no rows
        """
        if self._conn is None:
            raise RuntimeError("Session not active. Use 'async with DbSession()'")
        
        return await self._conn.fetchrow(query, *args)

    async def execute(self, query: str, *args: Any) -> str:
        """
        Execute a query without returning rows.
        
        Args:
            query: SQL query with $1, $2 placeholders
            *args: Query parameters
            
        Returns:
            Status string from asyncpg
        """
        if self._conn is None:
            raise RuntimeError("Session not active. Use 'async with DbSession()'")
        
        return await self._conn.execute(query, *args)

    async def executemany(self, query: str, args_list: list[tuple]) -> None:
        """
        Execute a query multiple times with different parameters.
        
        Args:
            query: SQL query with $1, $2 placeholders
            args_list: List of parameter tuples
        """
        if self._conn is None:
            raise RuntimeError("Session not active. Use 'async with DbSession()'")
        
        await self._conn.executemany(query, args_list)

    @property
    def connection(self) -> asyncpg.Connection:
        """Get the underlying connection (for advanced use)."""
        if self._conn is None:
            raise RuntimeError("Session not active. Use 'async with DbSession()'")
        return self._conn
