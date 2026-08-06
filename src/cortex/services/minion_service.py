"""Minion Service - wraps minion gateway with business logic."""

from __future__ import annotations

import logging
from typing import Any

from cortex.events import EventEmitter
from cortex.minions.interfaces import MinionEventHandler, MinionGateway, MinionRegistry
from cortex.minions.models import (
    MinionConfig,
    MinionEvent,
    MinionEventBatch,
    MinionInfo,
    MinionState,
)

logger = logging.getLogger(__name__)


class MinionService:
    """
    Service layer for minion management.

    Wraps the MinionGateway with:
    - Registry integration
    - Event processing
    - Fact extraction
    - Sequence gap detection
    - Health monitoring
    """

    def __init__(
        self,
        config: MinionConfig,
        gateway: MinionGateway,
        registry: MinionRegistry,
        event_bus: Any | None = None,
        memory_service: Any | None = None
    ):
        self._config = config
        self._gateway = gateway
        self._registry = registry
        self._memory_service = memory_service
        self._emitter = EventEmitter(event_bus, source_module="minion_service")

        # Sequence tracking for gap detection
        self._last_sequence: dict[str, int] = {}

        # Create event handler
        self._handler = MinionEventProcessor(event_bus, memory_service)

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
        await self._gateway.subscribe(self._handler)

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

        # Process based on event type
        if event.event_type == "location":
            await self._handle_location_event(event)
        elif event.event_type == "activity":
            await self._handle_activity_event(event)
        elif event.event_type == "calendar":
            await self._handle_calendar_event(event)

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

    async def _handle_location_event(self, event: MinionEvent) -> None:
        """Handle a location event."""
        payload = event.payload

        # Update registry with location
        info = await self._registry.get(event.minion_id)
        if info:
            info.last_location = payload.get("place") or f"{payload.get('latitude')},{payload.get('longitude')}"

        # Extract fact if memory service available
        if self._memory_service:
            fact = await self._memory_service._extract_location_facts(payload)
            if fact:
                await self._memory_service.store_fact(fact)

    async def _handle_activity_event(self, event: MinionEvent) -> None:
        """Handle an activity event."""
        payload = event.payload

        # Extract fact if memory service available
        if self._memory_service:
            fact = await self._memory_service._extract_activity_facts(payload)
            if fact:
                await self._memory_service.store_fact(fact)

    async def _handle_calendar_event(self, event: MinionEvent) -> None:
        """Handle a calendar event."""
        payload = event.payload

        # Extract fact if memory service available
        if self._memory_service:
            fact = await self._memory_service._extract_calendar_facts(payload)
            if fact:
                await self._memory_service.store_fact(fact)

    def is_connected(self) -> bool:
        """Check if the gateway is connected."""
        return self._gateway.is_connected()


class MinionEventProcessor(MinionEventHandler):
    """
    Process incoming minion events.

    Transforms raw events into structured facts and emits to the system.
    """

    def __init__(self, event_bus: Any | None = None, memory_service: Any | None = None):
        self._memory_service = memory_service
        self._emitter = EventEmitter(event_bus, source_module="minion_event_processor")

    async def handle_event(self, event: MinionEvent) -> None:
        """
        Handle a single minion event.

        Args:
            event: The minion event to process
        """
        # Process based on type
        if event.event_type == "location":
            await self._process_location(event)
        elif event.event_type == "activity":
            await self._process_activity(event)
        elif event.event_type == "battery":
            await self._process_battery(event)
        elif event.event_type == "app_usage":
            await self._process_app_usage(event)
        elif event.event_type == "calendar":
            await self._process_calendar(event)

    async def handle_batch(self, batch: MinionEventBatch) -> list[MinionEvent]:
        """
        Handle a batch of events.

        Args:
            batch: The batch of events

        Returns:
            Processed events
        """
        processed = []
        for event in batch.events:
            await self.handle_event(event)
            processed.append(event)
        return processed

    async def _process_location(self, event: MinionEvent) -> None:
        """Process a location event."""
        payload = event.payload
        await self._emitter.emit(
            "minion.location",
            {
                "minion_id": event.minion_id,
                "latitude": payload.get("latitude"),
                "longitude": payload.get("longitude"),
                "place": payload.get("place"),
                "accuracy": payload.get("accuracy"),
            },
        )

    async def _process_activity(self, event: MinionEvent) -> None:
        """Process an activity event."""
        payload = event.payload
        await self._emitter.emit(
            "minion.activity",
            {
                "minion_id": event.minion_id,
                "activity": payload.get("activity"),
                "confidence": payload.get("confidence"),
            },
        )

    async def _process_battery(self, event: MinionEvent) -> None:
        """Process a battery event."""
        payload = event.payload
        await self._emitter.emit(
            "minion.battery",
            {
                "minion_id": event.minion_id,
                "level": payload.get("level"),
                "is_charging": payload.get("is_charging"),
            },
        )

    async def _process_app_usage(self, event: MinionEvent) -> None:
        """Process an app usage event."""
        payload = event.payload
        await self._emitter.emit(
            "minion.app_usage",
            {
                "minion_id": event.minion_id,
                "app": payload.get("app"),
                "duration": payload.get("duration"),
            },
        )

    async def _process_calendar(self, event: MinionEvent) -> None:
        """Process a calendar event."""
        payload = event.payload
        await self._emitter.emit(
            "minion.calendar",
            {
                "minion_id": event.minion_id,
                "event": payload.get("title"),
                "start_time": payload.get("start_time"),
            },
        )
