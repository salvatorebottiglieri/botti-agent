"""Minion Service - wraps minion gateway with business logic."""

from __future__ import annotations

from typing import Any
import logging

from cortex.minions.models import MinionInfo, MinionEvent, MinionEventBatch, MinionConfig, MinionState
from cortex.minions.interfaces import MinionGateway, MinionEventHandler, MinionRegistry

logger = logging.getLogger(__name__)


class MinionService:
    """
    Service layer for minion management.
    
    Wraps the MinionGateway with:
    - Registry integration
    - Event processing
    - Fact extraction
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
        self._event_bus = event_bus
        self._memory_service = memory_service
        
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
        if self._event_bus:
            from cortex.events import BaseEvent
            await self._event_bus.publish(BaseEvent.create(
                event_type=f"minion.event.{event.event_type}",
                payload=event.to_dict() if hasattr(event, 'to_dict') else {
                    "minion_id": event.minion_id,
                    "event_type": event.event_type,
                    "payload": event.payload
                },
                source_module="minion_service"
            ))
    
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
        """
        # Update heartbeat
        await self.heartbeat()
        
        # Process based on event type
        if event.event_type == "location":
            await self._handle_location_event(event)
        elif event.event_type == "activity":
            await self._handle_activity_event(event)
        elif event.event_type == "calendar":
            await self._handle_calendar_event(event)
        
        # Emit to event bus
        if self._event_bus:
            from cortex.events import BaseEvent
            await self._event_bus.publish(BaseEvent.create(
                event_type="minion.event",
                payload={
                    "minion_id": event.minion_id,
                    "event_type": event.event_type,
                    "payload": event.payload
                },
                source_module="minion_service"
            ))
    
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
        self._event_bus = event_bus
        self._memory_service = memory_service
    
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
        
        # Emit to bus if available
        if self._event_bus:
            from cortex.events import BaseEvent
            await self._event_bus.publish(BaseEvent.create(
                event_type="minion.location",
                payload={
                    "minion_id": event.minion_id,
                    "latitude": payload.get("latitude"),
                    "longitude": payload.get("longitude"),
                    "place": payload.get("place"),
                    "accuracy": payload.get("accuracy")
                },
                source_module="minion_event_processor"
            ))
    
    async def _process_activity(self, event: MinionEvent) -> None:
        """Process an activity event."""
        payload = event.payload
        
        if self._event_bus:
            from cortex.events import BaseEvent
            await self._event_bus.publish(BaseEvent.create(
                event_type="minion.activity",
                payload={
                    "minion_id": event.minion_id,
                    "activity": payload.get("activity"),
                    "confidence": payload.get("confidence")
                },
                source_module="minion_event_processor"
            ))
    
    async def _process_battery(self, event: MinionEvent) -> None:
        """Process a battery event."""
        payload = event.payload
        
        if self._event_bus:
            from cortex.events import BaseEvent
            await self._event_bus.publish(BaseEvent.create(
                event_type="minion.battery",
                payload={
                    "minion_id": event.minion_id,
                    "level": payload.get("level"),
                    "is_charging": payload.get("is_charging")
                },
                source_module="minion_event_processor"
            ))
    
    async def _process_app_usage(self, event: MinionEvent) -> None:
        """Process an app usage event."""
        payload = event.payload
        
        if self._event_bus:
            from cortex.events import BaseEvent
            await self._event_bus.publish(BaseEvent.create(
                event_type="minion.app_usage",
                payload={
                    "minion_id": event.minion_id,
                    "app": payload.get("app"),
                    "duration": payload.get("duration")
                },
                source_module="minion_event_processor"
            ))
    
    async def _process_calendar(self, event: MinionEvent) -> None:
        """Process a calendar event."""
        payload = event.payload
        
        if self._event_bus:
            from cortex.events import BaseEvent
            await self._event_bus.publish(BaseEvent.create(
                event_type="minion.calendar",
                payload={
                    "minion_id": event.minion_id,
                    "event": payload.get("title"),
                    "start_time": payload.get("start_time")
                },
                source_module="minion_event_processor"
            ))