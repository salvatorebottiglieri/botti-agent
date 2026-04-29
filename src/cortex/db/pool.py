"""Async PostgreSQL connection pool management."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import asyncpg

from cortex.config.models import Settings

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def create_pool(settings: Settings) -> asyncpg.Pool:
    """
    Create a connection pool to PostgreSQL.
    
    Args:
        settings: Application settings with database_url
        
    Returns:
        asyncpg.Pool instance
        
    Raises:
        RuntimeError: If pool already exists
    """
    global _pool
    
    if _pool is not None:
        raise RuntimeError("Database pool already exists")
    
    logger.info(f"Creating database pool for: {settings.database_url.host}")
    
    _pool = await asyncpg.create_pool(
        host=settings.database_url.host or "localhost",
        port=settings.database_url.port or 5432,
        user=settings.database_url.username or "postgres",
        password=settings.database_url.password.get_secret_value() if settings.database_url.password else None,
        database=settings.database_url.path.lstrip("/") if settings.database_url.path else "cortex",
        min_size=5,
        max_size=20,
        command_timeout=60,
    )
    
    logger.info("Database pool created successfully")
    return _pool


async def get_pool() -> asyncpg.Pool:
    """
    Get the current database pool.
    
    Returns:
        asyncpg.Pool instance
        
    Raises:
        RuntimeError: If pool hasn't been created
    """
    if _pool is None:
        raise RuntimeError("Database pool not initialized. Call create_pool() first.")
    return _pool


async def close_pool() -> None:
    """Close the database pool."""
    global _pool
    
    if _pool is not None:
        logger.info("Closing database pool")
        await _pool.close()
        _pool = None
        logger.info("Database pool closed")


@asynccontextmanager
async def pool_transaction() -> AsyncGenerator[asyncpg.Connection, None]:
    """
    Execute a transaction using the pool.
    
    Example:
        async with pool_transaction() as conn:
            await conn.execute("INSERT INTO ...")
            await conn.execute("UPDATE ...")
        # Commits on success, rolls back on exception
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            yield conn
