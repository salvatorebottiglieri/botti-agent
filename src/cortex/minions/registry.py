"""In-memory minion registry implementation."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .interfaces import MinionRegistry
from .models import MinionInfo, MinionState


class InMemoryMinionRegistry(MinionRegistry):
    """
    In-memory implementation of MinionRegistry.


    Stores minion info in a dictionary.
    """

    def __init__(self) -> None:
        self._minions: dict[str, MinionInfo] = {}
        self._last_heartbeat: dict[str, datetime] = {}


    async def register(self, minion_id: str, info: MinionInfo) -> None:
        """"Register a new minion."""
        info.state = MinionState.ONLINE
        self._minions[minion_id] = info

    async def unregister(self, minion_id: str) -> None:
        """Unregister a minion."""
        self._minions.pop(minion_id, None)
        self._last_heartbeat.pop(minion_id, None)

    async def get(self, minion_id: str) -> MinionInfo | None:
        """Get minion info by ID."""
        return self._minions.get(minion_id)

    async def list_active(self) -> list[MinionInfo]:
        """List all active minions."""
        return [m for m in self._minions.values() if m.state == MinionState.ONLINE]

    async def list_all(self) -> list[MinionInfo]:
        """List all registered minions."""
        return list(self._minions.values())

    async def heartbeat(self, minion_id: str) -> None:
        """Update heartbeat timestamp."""
        self._last_heartbeat[minion_id] = datetime.now(UTC)
        if minion_id in self._minions:
            self._minions[minion_id].last_heartbeat = datetime.now(UTC)

    async def update_state(self, minion_id: str, state: str) -> None:
        """Update minion state."""
        if minion_id in self._minions:
            self._minions[minion_id].state = MinionState(state)



class PostgresMinionRegistry(MinionRegistry):
    """
    Postgres-backed implementation of MinionRegistry.

    Persists minion registrations to the minions table.
    """

    def __init__(self, pool: Any) -> None:
        """Initialize with a database connection pool."""
        self._pool = pool

    async def register(self, minion_id: str, info: MinionInfo) -> None:
        """Register a new minion in the database."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO minions (minion_id, minion_type, minion_version, metadata, last_heartbeat_at)
                VALUES ($1, $2, $3, $4, NOW())
                ON CONFLICT (minion_id) DO UPDATE SET
                    minion_type = EXCLUDED.minion_type,
                    last_heartbeat_at = NOW()
                """,
                minion_id,
                info.device_type,
                info.minion_version if hasattr(info, 'minion_version') else None,
                info.metadata if hasattr(info, 'metadata') else {},
            )

    async def unregister(self, minion_id: str) -> None:
        """Unregister a minion from the database."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM minions WHERE minion_id = $1",
                minion_id,
            )

    async def get(self, minion_id: str) -> MinionInfo | None:
        """Get minion info by ID from the database."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM minions WHERE minion_id = $1",
                minion_id,
            )
            if row:
                return MinionInfo.from_dict(dict(row))
            return None

    async def list_active(self) -> list[MinionInfo]:
        """List all active minions from the database."""
        # Active = has heartbeat within last 5 minutes
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM minions
                WHERE last_heartbeat_at > NOW() - INTERVAL '5 minutes'
                """
            )
            return [MinionInfo.from_dict(dict(row)) for row in rows]

    async def list_all(self) -> list[MinionInfo]:
        """List all registered minions from the database."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM minions")
            return [MinionInfo.from_dict(dict(row)) for row in rows]

    async def heartbeat(self, minion_id: str) -> None:
        """Update heartbeat timestamp for a minion."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE minions SET last_heartbeat_at = NOW() WHERE minion_id = $1",
                minion_id,
            )

    async def update_state(self, minion_id: str, state: str) -> None:
        """Update minion state."""
        # State changes are tracked in metadata for now
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE minions SET metadata = jsonb_set(metadata, '{state}', $2) WHERE minion_id = $1",
                minion_id,
                state,
            )
