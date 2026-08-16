"""Minion Service - wraps minion gateway with business logic."""

from __future__ import annotations

import logging
from typing import Any

from cortex.events import EventEmitter
from cortex.memory.fact_extractor import SUPPORTED_EVENT_TYPES
from cortex.memory.fact_store import FactStore
from cortex.memory.interfaces import FactExtractor
from cortex.minions.interfaces import MinionEventHandler, MinionGateway, MinionRegistry
from cortex.minions.models import (
    EventType,
    MinionConfig,
    MinionEvent,
    MinionEventBatch,
    MinionInfo,
    MinionState,
)

logger = logging.getLogger(__name__)

_MINION_EVENT_TYPE_TO_SENSORY = {
    "location.update": "location",
    "activity.detected": "activity",
    "calendar.event": "calendar",
    "app.usage": "app_usage",
    "payment": "payment",
    "call_log": "call_log",
}


def _sensory_event_type(event_type: EventType | str) -> str | None:
    """Map a minion event type to the plain sensory vocabulary FactExtractor dispatches on."""
    value = event_type.value if isinstance(event_type, EventType) else str(event_type)
    if value in SUPPORTED_EVENT_TYPES:
        return value
    return _MINION_EVENT_TYPE_TO_SENSORY.get(value)


class MinionService(MinionEventHandler):
    """
    Service layer for minion management.

    Wraps the MinionGateway with:
    - Registry integration
    - Event processing
    - Fact extraction (via FactExtractor)
    - Sequence gap detection
    - Health monitoring
    """

    def __init__(
        self,
        config: MinionConfig,
        gateway: MinionGateway,
        registry: MinionRegistry,
        event_bus: Any | None = None,
        fact_store: FactStore | None = None,
        fact_extractor: FactExtractor | None = None,
    ):
        self._config = config
        self._gateway = gateway
        self._registry = registry
        self._fact_store = fact_store
        self._fact_extractor = fact_extractor
        self._emitter = EventEmitter(event_bus, source_module="minion_service")

        # Sequence tracking for gap detection
        self._last_sequence: dict[str, int] = {}

    async def connect(self) -> None:
        """Connect to the message broker and start listening."""
        # Register with registry
        await self._registry.register(
            self._config.minion_id,
            MinionInfo(
                minion_id=self._config.minion_id,
                name=self._config.minion_name,
                device_type=self._config.device_type,
                capabilities={},
                state=MinionState.ONLINE,
                last_heartbeat=None
            )
        )

        # Connect gateway
        await self._gateway.connect()
        await self._gateway.subscribe(self)

        logger.info(f"MinionService connected for {self._config.minion_id}")

    async def disconnect(self) -> None:
        """Disconnect from the message broker."""
        await self._gateway.disconnect()

        # Update registry
        await self._registry.update_state(self._config.minion_id, MinionState.OFFLINE.value)

        logger.info(f"MinionService disconnected for {self._config.minion_id}")

    async def send_event(self, event: MinionEvent) -> None:
        """
        Send an event to the event bus.

        Args:
            event: The event to send
        """
        payload = event.to_dict() if hasattr(event, 'to_dict') else {
            "minion_id": event.minion_id,
            "event_type": event.event_type,
            "payload": event.payload,
        }
        await self._emitter.emit(f"minion.event.{event.event_type}", payload)

    async def get_active_minions(self) -> list[MinionInfo]:
        """Get all active minions."""
        return await self._registry.list_active()

    async def heartbeat(self) -> None:
        """Send a heartbeat to the registry."""
        await self._registry.heartbeat(self._config.minion_id)

    async def handle_event(self, event: MinionEvent) -> None:
        """
        Handle an incoming minion event.

        Processes the event and may emit derived events or facts.
        Also detects sequence gaps.
        """
        # Check for sequence gaps
        await self._check_sequence_gap(event)

        # Update heartbeat
        await self.heartbeat()

        # Translate the minion event type to the plain sensory vocabulary
        # FactExtractor dispatches on (EventType values are dotted, e.g.
        # "location.update" -> "location").
        sensory_type = _sensory_event_type(event.event_type)

        # Track last known location in the registry
        if sensory_type == "location":
            info = await self._registry.get(event.minion_id)
            if info:
                info.last_location = event.payload.get("place") or (
                    f"{event.payload.get('latitude')},{event.payload.get('longitude')}"
                )

        # Extract facts from the event via the FactExtractor
        if sensory_type and self._fact_extractor and self._fact_store:
            for fact in self._fact_extractor.extract_from_event_type(
                sensory_type, event.payload
            ):
                await self._fact_store.add_fact(fact)

        await self._emitter.emit(
            "minion.event",
            {
                "minion_id": event.minion_id,
                "event_type": event.event_type,
                "payload": event.payload,
            },
        )

    async def _check_sequence_gap(self, event: MinionEvent) -> None:
        """
        Check for sequence gaps in incoming events.

        Logs a warning if sequence numbers are not contiguous.
        No retransmit in v1.
        """
        if event.sequence_number == 0:
            # No sequence tracking for this event
            return

        last_seq = self._last_sequence.get(event.minion_id, 0)
        if last_seq > 0 and event.sequence_number != last_seq + 1:
            gap = event.sequence_number - last_seq - 1
            logger.warning(
                f"Sequence gap detected for minion {event.minion_id}: "
                f"expected {last_seq + 1}, got {event.sequence_number} "
                f"(gap of {gap} event(s))"
            )

        # Update last sequence
        self._last_sequence[event.minion_id] = event.sequence_number

    async def handle_batch(self, batch: MinionEventBatch) -> list[MinionEvent]:
        """Handle a batch of events."""
        processed = []
        for event in batch.events:
            await self.handle_event(event)
            processed.append(event)
        return processed

    def is_connected(self) -> bool:
        """Check if the gateway is connected."""
        return self._gateway.is_connected()
