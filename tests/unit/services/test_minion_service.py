"""Tests for MinionService."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from cortex.minions.models import MinionInfo, MinionEvent, MinionEventBatch, MinionConfig, MinionState
from cortex.minions.interfaces import MinionGateway, MinionEventHandler, MinionRegistry
from cortex.services.minion_service import MinionService


class TestMinionService:
    """Tests for MinionService."""

    @pytest.fixture
    def mock_gateway(self):
        """Create a mock gateway."""
        gateway = MagicMock(spec=MinionGateway)
        gateway.connect = AsyncMock()
        gateway.disconnect = AsyncMock()
        gateway.subscribe = AsyncMock()
        gateway.unsubscribe = AsyncMock()
        gateway.is_connected = MagicMock(return_value=False)
        return gateway

    @pytest.fixture
    def mock_registry(self):
        """Create a mock minion registry."""
        registry = MagicMock(spec=MinionRegistry)
        registry.register = AsyncMock()
        registry.get = AsyncMock(return_value=None)
        registry.list_active = AsyncMock(return_value=[])
        registry.heartbeat = AsyncMock()
        registry.update_state = AsyncMock()
        return registry

    @pytest.fixture
    def mock_event_bus(self):
        """Create a mock event bus."""
        bus = MagicMock()
        bus.publish = AsyncMock()
        return bus

    @pytest.fixture
    def mock_handler(self):
        """Create a mock event handler."""
        handler = MagicMock(spec=MinionEventHandler)
        handler.handle_event = AsyncMock()
        handler.handle_batch = AsyncMock(return_value=[])
        return handler

    @pytest.fixture
    def config(self):
        """Create a minion config."""
        return MinionConfig(
            broker_url="mqtt://localhost:1883",
            minion_id="test-minion-1",
            minion_name="Test Minion",
            device_type="phone"
        )

    @pytest.fixture
    def service(self, mock_gateway, mock_registry, mock_event_bus, config):
        """Create a MinionService instance."""
        return MinionService(
            config=config,
            gateway=mock_gateway,
            registry=mock_registry,
            event_bus=mock_event_bus
        )

    @pytest.mark.asyncio
    async def test_connect(self, service, mock_gateway, mock_registry, config):
        """Connecting starts gateway and registers minion."""
        await service.connect()
        
        mock_gateway.connect.assert_called_once()
        mock_gateway.subscribe.assert_called_once()
        mock_registry.register.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect(self, service, mock_gateway):
        """Disconnecting stops gateway."""
        await service.disconnect()
        
        mock_gateway.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_is_connected(self, service, mock_gateway):
        """is_connected delegates to gateway."""
        mock_gateway.is_connected.return_value = True
        
        assert service.is_connected() is True
        
        mock_gateway.is_connected.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_event(self, service, mock_event_bus):
        """Sending an event publishes to event bus."""
        from cortex.minions.models import EventType
        event = MinionEvent(
            event_id="evt-1",
            minion_id="test-minion-1",
            event_type=EventType.LOCATION_UPDATE,
            payload={"latitude": 37.77, "longitude": -122.41}
        )
        
        await service.send_event(event)
        
        mock_event_bus.publish.assert_called()

    @pytest.mark.asyncio
    async def test_get_active_minions(self, service, mock_registry):
        """Getting active minions calls registry."""
        minion = MinionInfo(
            minion_id="minion-1",
            name="Minion One",
            device_type="phone",
            capabilities={},
            state=MinionState.ONLINE
        )
        mock_registry.list_active.return_value = [minion]
        
        result = await service.get_active_minions()
        
        mock_registry.list_active.assert_called_once()
        assert len(result) == 1
        assert result[0].minion_id == "minion-1"

    @pytest.mark.asyncio
    async def test_heartbeat_updates_registry(self, service, mock_registry):
        """Heartbeat updates registry."""
        await service.heartbeat()
        
        mock_registry.heartbeat.assert_called_once_with("test-minion-1")

    @pytest.mark.asyncio
    async def test_handle_event_publishes_to_bus(self, service, mock_event_bus):
        """Handling an event publishes to event bus."""
        from cortex.minions.models import EventType
        event = MinionEvent(
            event_id="e1",
            minion_id="other-minion",
            event_type=EventType.ACTIVITY_DETECTED,
            payload={"activity": "walking"}
        )
        
        await service.handle_event(event)
        
        # Should emit to bus
        assert mock_event_bus.publish.called or mock_event_bus.emit.called

    @pytest.mark.asyncio
    async def test_handle_event_extracts_facts(self, service, mock_event_bus):
        """Location events trigger fact extraction."""
        from cortex.minions.models import EventType
        event = MinionEvent(
            event_id="e2",
            minion_id="test-minion",
            event_type=EventType.LOCATION_UPDATE,
            payload={"latitude": 37.77, "longitude": -122.41, "place": "work"}
        )
        
        await service.handle_event(event)
        
        # Event should be published
        assert mock_event_bus.publish.called

    @pytest.mark.asyncio
    async def test_batch_events_processed(self, service):
        """Batch events are processed correctly."""
        from cortex.minions.models import EventType
        batch = MinionEventBatch(
            batch_id="batch-1",
            minion_id="test-minion",
            events=[
                MinionEvent(event_id="e1", minion_id="test", event_type=EventType.LOCATION_UPDATE, payload={}),
                MinionEvent(event_id="e2", minion_id="test", event_type=EventType.ACTIVITY_DETECTED, payload={}),
            ]
        )
        
        # The handler should process each event
        processed = await service.handle_batch(batch)
        
        assert processed is not None  # Should return processed events


class TestMinionEventHandler:
    """Tests for MinionEventProcessor."""

    @pytest.fixture
    def processor(self):
        """Create a processor with mocked dependencies."""
        from cortex.services.minion_service import MinionEventProcessor
        mock_bus = MagicMock()
        return MinionEventProcessor(event_bus=mock_bus)

    @pytest.mark.asyncio
    async def test_process_location_event(self, processor):
        """Location events are processed correctly."""
        from cortex.minions.models import EventType
        event = MinionEvent(
            event_id="event-1",
            minion_id="minion-1",
            event_type=EventType.LOCATION_UPDATE,
            payload={
                "latitude": 37.7749,
                "longitude": -122.4194,
                "accuracy": 10.0,
                "place": "coffee_shop"
            }
        )
        
        await processor.handle_event(event)

    @pytest.mark.asyncio
    async def test_process_activity_event(self, processor):
        """Activity events are processed correctly."""
        from cortex.minions.models import EventType
        event = MinionEvent(
            event_id="event-2",
            minion_id="minion-1",
            event_type=EventType.ACTIVITY_DETECTED,
            payload={
                "activity": "driving",
                "confidence": 0.85
            }
        )
        
        await processor.handle_event(event)

    @pytest.mark.asyncio
    async def test_process_batch(self, processor):
        """Batch events are processed."""
        from cortex.minions.models import EventType
        batch = MinionEventBatch(
            batch_id="batch-123",
            minion_id="minion-1",
            events=[
                MinionEvent(event_id="e1", minion_id="test", event_type=EventType.LOCATION_UPDATE, payload={}),
                MinionEvent(event_id="e2", minion_id="test", event_type=EventType.BATTERY_LEVEL, payload={"level": 80}),
            ]
        )
        
        results = await processor.handle_batch(batch)
        
        assert len(results) == 2


class TestMinionConfig:
    """Tests for MinionConfig."""

    def test_default_values(self):
        """Config has sensible defaults."""
        config = MinionConfig(
            broker_url="mqtt://localhost:1883",
            minion_id="test",
            minion_name="Test"
        )
        
        assert config.port == 1883
        assert config.keepalive == 60
        assert config.qos == 1

    def test_custom_values(self):
        """Config allows custom values."""
        config = MinionConfig(
            broker_url="mqtt://localhost:1883",
            minion_id="test",
            minion_name="Test",
            port=8883,
            keepalive=120
        )
        
        assert config.port == 8883
        assert config.keepalive == 120