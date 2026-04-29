"""Event bus exceptions."""


class EventBusError(Exception):
    """Base exception for event bus errors."""
    pass


class EventHandlerError(EventBusError):
    """Error in an event handler."""
    pass


class EventPublishError(EventBusError):
    """Failed to publish an event."""
    pass


class EventSubscriptionError(EventBusError):
    """Failed to subscribe to an event."""
    pass
