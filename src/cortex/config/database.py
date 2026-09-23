"""Database settings slice."""

from __future__ import annotations

from pydantic import Field

from cortex.config.base import CortexSettings


class DatabaseSettings(CortexSettings):
    """PostgreSQL connection and pool settings.

    Field names are the environment variable names (``DATABASE_URL``,
    ``DB_POOL_MIN_SIZE``, ``DB_POOL_MAX_SIZE``, ``DB_POOL_TIMEOUT``), so the
    slice deliberately has no ``env_prefix``.
    """

    database_url: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/cortex",
        description="PostgreSQL connection URL",
    )
    db_pool_min_size: int = Field(default=5, ge=1)
    db_pool_max_size: int = Field(default=20, ge=1)
    db_pool_timeout: int = Field(default=30, ge=1)
