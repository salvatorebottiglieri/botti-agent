"""Minion module interfaces (ABCs)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from .models import MinionEvent, MinionEventBatch, MinionInfo

if TYPE_CHECKING:
    pass


class MinionEventHandler(ABC):
    """
    Handle incoming minion events.

    Implement this to process events from minions.
    """

    @abstractmethod
    async def handle_event(self, event: MinionEvent) -> None:
        """
        Handle a single event from a minion.

        Args:
            event: The minion event to process.
        """
        ...

    @abstractmethod
    async def handle_batch(self, batch: MinionEventBatch) -> list[MinionEvent]:
        """
        Handle a batch of events from a minion.

        Args:
            batch: The batch of events to process.

        Returns:
            List of processed events (for acknowledgment).
        """
        ...


class MinionGateway(ABC):
    """
    Interface for receiving minion events.

    Implementations connect to MQTT and forward events to handlers.
    """

    @abstractmethod
    async def connect(self) -> None:
        """Connect to the message broker and start listening."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the message broker."""
        ...

    @abstractmethod
    async def subscribe(self, handler: MinionEventHandler) -> None:
        """
        Subscribe to minion events with a handler.

        Args:
            handler: The handler to receive events.
        """
        ...

    @abstractmethod
    async def unsubscribe(self, handler: MinionEventHandler) -> None:
        """
        Unsubscribe a handler from events.

        Args:
            handler: The handler to remove.
        """
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if the gateway is currently connected."""
        ...


class MinionRegistry(ABC):
    """
    Track registered minions.

    Stores minion metadata and connection state.
    """

    @abstractmethod
    async def register(self, minion_id: str, info: MinionInfo) -> None:
        """
        Register a new minion.

        Args:
            minion_id: Unique identifier for the minion.
            info: Minion information and capabilities.
        """
        ...

    @abstractmethod
    async def unregister(self, minion_id: str) -> None:
        """
        Unregister a minion.

        Args:
            minion_id: The minion to remove.
        """
        ...

    @abstractmethod
    async def get(self, minion_id: str) -> MinionInfo | None:
        """
        Get minion info by ID.

        Args:
            minion_id: The minion to look up.

        Returns:
            MinionInfo if found, None otherwise.
        """
        ...

    @abstractmethod
    async def list_active(self) -> list[MinionInfo]:
        """
        List all active (online) minions.

        Returns:
            List of active minion info.
        """
        ...

    @abstractmethod
    async def list_all(self) -> list[MinionInfo]:
        """
        List all registered minions.

        Returns:
            List of all minion info.
        """
        ...

    @abstractmethod
    async def heartbeat(self, minion_id: str) -> None:
        """
        Update heartbeat timestamp for a minion.

        Args:
            minion_id: The minion that sent a heartbeat.
        """
        ...

    @abstractmethod
    async def update_state(self, minion_id: str, state: str) -> None:
        """
        Update minion state.

        Args:
            minion_id: The minion to update.
            state: New state (online, away, offline).
        """
        ...