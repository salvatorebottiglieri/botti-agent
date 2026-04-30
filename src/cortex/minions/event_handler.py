"""Event handler for processing minion events into facts."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .interfaces import MinionEventHandler
from .models import MinionEvent, MinionEventBatch

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class MinionEventProcessor(MinionEventHandler):
    """
    Processes minion events and extracts facts.

    Currently a stub that logs events. In Wave 3, this will
    integrate with MemoryService to store facts.
    """

    def __init__(self) -> None:
        """Initialize the processor."""
        self._processed_count = 0

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