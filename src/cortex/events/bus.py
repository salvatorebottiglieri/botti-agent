"""In-memory async event bus implementation."""

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable
from contextlib import asynccontextmanager

from cortex.events.base import BaseEvent
from cortex.events.exceptions import EventBusError

logger = logging.getLogger(__name__)

EventHandler = Callable[[BaseEvent], Awaitable[None]]


@dataclass
class Subscription:
    """Represents an active event subscription."""
    id: str
    event_type: str
    handler: EventHandler
    subscribed_at: float = field(default_factory=lambda: asyncio.get_event_loop().time())


@dataclass
class EventBusState:
    """Internal state for the event bus."""
    subscriptions: dict[str, list[Subscription]] = field(default_factory=lambda: defaultdict(list))
    handlers: dict[str, EventHandler] = field(default_factory=dict)
    subscription_counter: int = 0
    running: bool = False


class EventBus:
    """
    In-memory async event bus for indirect module communication.
    
    The River — modules communicate by publishing and subscribing to events
    rather than calling each other directly.
    
    Example:
        event_bus = EventBus()
        await event_bus.start()
        
        async def handle_user_message(event: BaseEvent):
            print(f"User said: {event.payload['content']}")
        
        await event_bus.subscribe("user.message", handle_user_message)
        await event_bus.publish(BaseEvent.create(
            event_type="user.message",
            payload={"content": "Hello!"},
            source_module="api"
        ))
        
        await event_bus.stop()
    """

    def __init__(self) -> None:
        self._state = EventBusState()
        self._lock = asyncio.Lock()
        self._subscription_lock = asyncio.Lock()
        self._log = logger

    async def start(self) -> None:
        """Start the event bus."""
        async with self._lock:
            if self._state.running:
                raise EventBusError("Event bus already running")
            self._state.running = True
            self._log.info("Event bus started")

    async def stop(self) -> None:
        """Stop the event bus and clean up subscriptions."""
        async with self._lock:
            if not self._state.running:
                raise EventBusError("Event bus not running")
            self._state.running = False
            # Clear all subscriptions
            self._state.subscriptions.clear()
            self._state.handlers.clear()
            self._log.info("Event bus stopped")

    @asynccontextmanager
    async def subscribed(self, event_type: str, handler: EventHandler):
        """
        Context manager for subscribing and automatically unsubscribing.
        
        Example:
            async with event_bus.subscribed("user.message", handler):
                # handler is active
                await event_bus.publish(...)
            # handler is automatically unsubscribed
        """
        sub = await self.subscribe(event_type, handler)
        try:
            yield sub
        finally:
            await self.unsubscribe(sub.id)

    async def subscribe(
        self,
        event_type: str | "*",
        handler: EventHandler,
    ) -> Subscription:
        """
        Subscribe to an event type.
        
        Args:
            event_type: The event type to subscribe to, or "*" for all events
            handler: Async callable that handles the event
            
        Returns:
            Subscription object that can be used to unsubscribe
        """
        if not self._state.running:
            raise EventBusError("Event bus not running")
        
        async with self._subscription_lock:
            self._state.subscription_counter += 1
            sub_id = f"sub_{self._state.subscription_counter}"
            
            sub = Subscription(
                id=sub_id,
                event_type=event_type,
                handler=handler,
            )
            
            self._state.subscriptions[event_type].append(sub)
            self._state.handlers[sub_id] = handler
            self._log.debug(f"Subscribed {sub_id} to '{event_type}'")
            
        return sub

    async def unsubscribe(self, subscription_id: str) -> None:
        """
        Unsubscribe from an event.
        
        Args:
            subscription_id: The ID of the subscription to remove
        """
        async with self._subscription_lock:
            for event_type, subs in self._state.subscriptions.items():
                self._state.subscriptions[event_type] = [
                    s for s in subs if s.id != subscription_id
                ]
            self._state.handlers.pop(subscription_id, None)
            self._log.debug(f"Unsubscribed {subscription_id}")

    async def publish(self, event: BaseEvent) -> None:
        """
        Publish an event to all subscribers.
        
        Args:
            event: The event to publish
            
        Note:
            All matching handlers are called concurrently.
            Errors in handlers are logged but don't prevent other handlers from running.
        """
        if not self._state.running:
            raise EventBusError("Event bus not running")
        
        async with self._subscription_lock:
            # Get current subscriptions snapshot
            subscriptions = list(self._state.subscriptions.get(event.type, []))
            # Also get wildcard subscribers
            subscriptions.extend(self._state.subscriptions.get("*", []))

        if not subscriptions:
            self._log.debug(f"No subscribers for event type: {event.type}")
            return
        
        self._log.debug(
            f"Publishing event '{event.type}' to {len(subscriptions)} subscribers"
        )
        
        # Call all handlers concurrently
        tasks = []
        for sub in subscriptions:
            tasks.append(self._safe_handle(sub.handler, event))
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _safe_handle(self, handler: EventHandler, event: BaseEvent) -> None:
        """Safely execute a handler, logging any errors."""
        try:
            await handler(event)
        except Exception as e:
            self._log.error(
                f"Error in event handler for '{event.type}': {e}",
                exc_info=True
            )

    async def get_subscription_count(self, event_type: str | "*" = "*") -> int:
        """Get the number of subscriptions for an event type."""
        async with self._subscription_lock:
            return len(self._state.subscriptions.get(event_type, []))

    @property
    def is_running(self) -> bool:
        """Check if the event bus is running."""
        return self._state.running
