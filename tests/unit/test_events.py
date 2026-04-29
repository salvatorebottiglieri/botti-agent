"""Tests for the Event Bus."""

import asyncio
import pytest

from cortex.events import EventBus, BaseEvent, EventTypes


@pytest.fixture
async def event_bus():
    """Create a fresh event bus for each test."""
    bus = EventBus()
    await bus.start()
    yield bus
    await bus.stop()


class TestEventBus:
    """Test cases for EventBus."""

    @pytest.mark.asyncio
    async def test_start_stop(self):
        """Test event bus can be started and stopped."""
        bus = EventBus()
        
        assert not bus.is_running
        
        await bus.start()
        assert bus.is_running
        
        await bus.stop()
        assert not bus.is_running

    @pytest.mark.asyncio
    async def test_publish_without_subscribers(self, event_bus):
        """Test publishing without subscribers doesn't raise."""
        event = BaseEvent.create(
            event_type="test.event",
            payload={"data": "test"},
            source_module="test"
        )
        await event_bus.publish(event)  # Should not raise

    @pytest.mark.asyncio
    async def test_subscribe_and_receive(self, event_bus):
        """Test that subscribers receive published events."""
        received_events = []

        async def handler(event: BaseEvent):
            received_events.append(event)

        await event_bus.subscribe("test.event", handler)

        event = BaseEvent.create(
            event_type="test.event",
            payload={"data": "hello"},
            source_module="test"
        )
        await event_bus.publish(event)

        # Give handlers time to execute
        await asyncio.sleep(0.01)

        assert len(received_events) == 1
        assert received_events[0].type == "test.event"
        assert received_events[0].payload["data"] == "hello"

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self, event_bus):
        """Test multiple subscribers for the same event type."""
        results = {"handler1": [], "handler2": []}

        async def handler1(event: BaseEvent):
            results["handler1"].append(event)

        async def handler2(event: BaseEvent):
            results["handler2"].append(event)

        await event_bus.subscribe("test.event", handler1)
        await event_bus.subscribe("test.event", handler2)

        await event_bus.publish(BaseEvent.create(
            event_type="test.event",
            payload={},
            source_module="test"
        ))

        await asyncio.sleep(0.01)

        assert len(results["handler1"]) == 1
        assert len(results["handler2"]) == 1

    @pytest.mark.asyncio
    async def test_wildcard_subscription(self, event_bus):
        """Test wildcard (*) subscriptions receive all events."""
        received = []

        async def handler(event: BaseEvent):
            received.append(event)

        await event_bus.subscribe("*", handler)

        await event_bus.publish(BaseEvent.create(
            event_type="user.message",
            payload={},
            source_module="test"
        ))
        await event_bus.publish(BaseEvent.create(
            event_type="location",
            payload={},
            source_module="test"
        ))

        await asyncio.sleep(0.01)

        assert len(received) == 2

    @pytest.mark.asyncio
    async def test_unsubscribe(self, event_bus):
        """Test that unsubscribing stops event delivery."""
        received = []

        async def handler(event: BaseEvent):
            received.append(event)

        sub = await event_bus.subscribe("test.event", handler)
        await event_bus.unsubscribe(sub.id)

        await event_bus.publish(BaseEvent.create(
            event_type="test.event",
            payload={},
            source_module="test"
        ))

        await asyncio.sleep(0.01)

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_subscribed_context_manager(self, event_bus):
        """Test the subscribed context manager."""
        received = []

        async def handler(event: BaseEvent):
            received.append(event)

        async with event_bus.subscribed("test.event", handler):
            await event_bus.publish(BaseEvent.create(
                event_type="test.event",
                payload={},
                source_module="test"
            ))
            await asyncio.sleep(0.01)

        assert len(received) == 1

        # After context, handler should not receive
        await event_bus.publish(BaseEvent.create(
            event_type="test.event",
            payload={},
            source_module="test"
        ))

        await asyncio.sleep(0.01)

        assert len(received) == 1  # Still 1, not 2

    @pytest.mark.asyncio
    async def test_event_metadata(self, event_bus):
        """Test that event metadata is preserved."""
        metadata_received = None

        async def handler(event: BaseEvent):
            nonlocal metadata_received
            metadata_received = event.metadata

        await event_bus.subscribe("test.event", handler)

        event = BaseEvent.create(
            event_type="test.event",
            payload={"data": "test"},
            source_module="my_module",
            salience=0.8
        )
        await event_bus.publish(event)

        await asyncio.sleep(0.01)

        assert metadata_received is not None
        assert metadata_received.source_module == "my_module"
        assert metadata_received.salience == 0.8
        assert metadata_received.trace_id is not None

    @pytest.mark.asyncio
    async def test_handler_error_doesnt_crash_bus(self, event_bus):
        """Test that handler errors don't crash the event bus."""
        errors = []

        async def bad_handler(event: BaseEvent):
            raise ValueError("Handler error!")

        async def good_handler(event: BaseEvent):
            errors.append(event)

        await event_bus.subscribe("test.event", bad_handler)
        await event_bus.subscribe("test.event", good_handler)

        await event_bus.publish(BaseEvent.create(
            event_type="test.event",
            payload={},
            source_module="test"
        ))

        await asyncio.sleep(0.01)

        # Good handler should still receive
        assert len(errors) == 1

    @pytest.mark.asyncio
    async def test_publish_not_running_raises(self):
        """Test that publishing to non-running bus raises."""
        bus = EventBus()

        with pytest.raises(Exception):
            await bus.publish(BaseEvent.create(
                event_type="test",
                payload={},
                source_module="test"
            ))


class TestBaseEvent:
    """Test cases for BaseEvent."""

    def test_create_factory(self):
        """Test BaseEvent.create factory method."""
        event = BaseEvent.create(
            event_type="user.message",
            payload={"content": "Hello!"},
            source_module="api"
        )

        assert event.type == "user.message"
        assert event.payload["content"] == "Hello!"
        assert event.metadata.source_module == "api"
        assert event.metadata.timestamp is not None
        assert event.metadata.trace_id is not None

    def test_event_types(self):
        """Test EventTypes enum."""
        assert EventTypes.USER_MESSAGE == "user.message"
        assert EventTypes.GOAL_CREATED == "goal.created"
        assert EventTypes.LOCATION == "location"


class TestSubscription:
    """Test cases for Subscription."""

    @pytest.mark.asyncio
    async def test_subscription_has_id(self, event_bus):
        """Test that subscriptions have unique IDs."""
        async def handler(e):
            pass

        sub1 = await event_bus.subscribe("test", handler)
        sub2 = await event_bus.subscribe("test", handler)

        assert sub1.id != sub2.id
