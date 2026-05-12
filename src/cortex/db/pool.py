"""Async PostgreSQL connection pool management."""

import logging
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from urllib.parse import urlparse

import asyncpg

from cortex.config.models import Settings

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


def _parse_db_url(url: str) -> dict:
    """
    Parse a PostgreSQL connection URL into components.

    Supports formats:
    - postgresql://user:pass@host:port/db
    - postgres://user:pass@host:port/db
    - postgresql://user:pass@host/db
    - postgresql://host/db
    """
    parsed = urlparse(url)

    # Default values
    host = "localhost"
    port = 5432
    user = "postgres"
    password = None
    database = "cortex"

    # Extract from URL
    if parsed.hostname:
        host = parsed.hostname
    if parsed.port:
        port = parsed.port
    if parsed.username:
        user = parsed.username
    if parsed.password:
        password = parsed.password
    if parsed.path:
        database = parsed.path.lstrip("/") or "cortex"

    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "database": database,
    }


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

    db_config = _parse_db_url(settings.database_url)

    logger.info(f"Creating database pool for: {db_config['host']}")

    _pool = await asyncpg.create_pool(
        host=db_config["host"],
        port=db_config["port"],
        user=db_config["user"],
        password=db_config["password"],
        database=db_config["database"],
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        command_timeout=settings.db_pool_timeout,
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
