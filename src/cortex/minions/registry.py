"""In-memory minion registry implementation."""
from __future__ import annotations

from datetime import datetime, timezone
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
        """Register a new minion."""
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
        self._last_heartbeat[minion_id] = datetime.now(timezone.utc)
        if minion_id in self._minions:
            self._minions[minion_id].last_heartbeat = datetime.now(timezone.utc)

    async def update_state(self, minion_id: str, state: str) -> None:
        """Update minion state."""
        if minion_id in self._minions:
            self._minions[minion_id].state = MinionState(state)