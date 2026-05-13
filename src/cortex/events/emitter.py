"""EventEmitter — single seam for publishing events to the bus.

Owns BaseEvent construction, the None-bus guard, and the publish-failure
policy (log warning, never raise). Construct one per caller in `__init__`
with the source_module name; call `emit(event_type, payload)` from then on.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from cortex.events.base import BaseEvent

if TYPE_CHECKING:
    from cortex.events.bus import EventBus

logger = logging.getLogger(__name__)


class EventEmitter:
    def __init__(self, bus: EventBus | None, source_module: str):
        self._bus = bus
        self._source_module = source_module

    async def emit(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        session_id: UUID | None = None,
        trace_id: str | None = None,
        salience: float = 0.5,
    ) -> None:
        if self._bus is None:
            return
        try:
            event = BaseEvent.create(
                event_type=event_type,
                payload=payload or {},
                source_module=self._source_module,
                session_id=session_id,
                trace_id=trace_id,
                salience=salience,
            )
            await self._bus.publish(event)
        except Exception as e:
            logger.warning(f"Failed to emit event {event_type}: {e}")
