"""Event handler for processing minion events into facts."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .interfaces import MinionEventHandler
from .models import MinionEvent, MinionEventBatch

if TYPE_CHECKING:
    from cortex.events.bus import EventBus

logger = logging.getLogger(__name__)


class MinionEventProcessor(MinionEventHandler):
    """
    Processes minion events and extracts facts.

    Transforms minion events into BaseEvents on the Cortex event bus.
    Each event type maps to a specific event type on the bus.
    """

    def __init__(self, event_bus: EventBus | None = None) -> None:
        """Initialize the processor with optional event bus."""
        self._processed_count = 0
        self._event_bus = event_bus

    async def handle_event(self, event: MinionEvent) -> None:
        """Handle a single event from a minion."""
        logger.info(
            "Processing minion event",
            extra={
                "event_id": event.event_id,
                "minion_id": event.minion_id,
                "event_type": event.event_type.value,
            },
        )
        self._processed_count += 1

        # Emit to event bus if available
        if self._event_bus:
            await self._emit_to_bus(event)

    async def handle_batch(self, batch: MinionEventBatch) -> list[MinionEvent]:
        """Handle a batch of events from a minion."""
        logger.info(
            "Processing minion event batch",
            extra={
                "batch_id": batch.batch_id,
                "minion_id": batch.minion_id,
                "event_count": len(batch.events),
            },
        )
        for event in batch.events:
            await self.handle_event(event)
        return batch.events

    @property
    def processed_count(self) -> int:
        """Get the number of processed events."""
        return self._processed_count

    def reset_stats(self) -> None:
        """Reset processing statistics."""
        self._processed_count = 0

    async def _emit_to_bus(self, event: MinionEvent) -> None:
        """Emit event to the Cortex event bus."""
        if self._event_bus is None:
            return

        from cortex.events import BaseEvent

        # Map event types to bus event types
        event_type_map = {
            "location": "minion.location",
            "location.update": "minion.location",
            "activity": "minion.activity",
            "activity.detected": "minion.activity",
            "battery": "minion.battery",
            "battery.level": "minion.battery",
            "app_usage": "minion.app_usage",
            "calendar": "minion.calendar",
            "payment": "minion.payment",
            "screen_activity": "minion.screen_activity",
            "application_focus": "minion.application_focus",
            "keyboard_activity": "minion.keyboard_activity",
            "network_status": "minion.network_status",
        }

        bus_type = event_type_map.get(event.event_type, f"minion.{event.event_type}")

        await self._event_bus.publish(BaseEvent.create(
            event_type=bus_type,
            payload={
                "minion_id": event.minion_id,
                "event_type": event.event_type,
                "payload": event.payload,
                "timestamp": event.timestamp.isoformat() if event.timestamp else None,
            },
            source_module="minion_event_processor",
        ))
