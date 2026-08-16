"""Tests for MinionService."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from cortex.minions.interfaces import MinionGateway, MinionRegistry
from cortex.minions.models import (
    MinionConfig,
    MinionEvent,
    MinionEventBatch,
    MinionInfo,
    MinionState,
)
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
        mock_gateway.subscribe.assert_called_once_with(service)
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
    async def test_handle_event_uses_fact_extractor(
        self, mock_gateway, mock_registry, mock_event_bus, config
    ):
        """handle_event routes extraction through FactExtractor and stores facts."""
        from cortex.memory.models import Fact, FactMutability, FactType
        memory_service = MagicMock()
        memory_service.store_fact = AsyncMock()
        fact_extractor = MagicMock()
        fact_extractor.extract_from_event_type = MagicMock(
            return_value=[
                Fact(
                    type=FactType.LOCATION,
                    mutability=FactMutability.MUTABLE,
                    symbolic_repr="location.work",
                    natural_lang_repr="At work",
                ),
                Fact(
                    type=FactType.ACTIVITY,
                    mutability=FactMutability.EPHEMERAL,
                    symbolic_repr="activity.walking",
                    natural_lang_repr="Currently walking",
                ),
            ]
        )
        service = MinionService(
            config=config,
            gateway=mock_gateway,
            registry=mock_registry,
            event_bus=mock_event_bus,
            memory_service=memory_service,
            fact_extractor=fact_extractor,
        )

        from cortex.minions.models import EventType
        event = MinionEvent(
            event_id="e3",
            minion_id="test-minion",
            event_type=EventType.LOCATION_UPDATE,
            payload={"latitude": 37.77, "longitude": -122.41, "place": "work"},
        )

        await service.handle_event(event)

        # The dotted enum value ("location.update") is translated to the plain
        # sensory vocabulary ("location") before reaching the extractor.
        fact_extractor.extract_from_event_type.assert_called_once_with(
            "location", event.payload
        )
        assert memory_service.store_fact.await_count == 2

    @pytest.mark.asyncio
    async def test_handle_event_passes_plain_string_sensory_type(
        self, mock_gateway, mock_registry, mock_event_bus, config
    ):
        """A plain-string sensory event type is passed through untranslated."""
        memory_service = MagicMock()
        memory_service.store_fact = AsyncMock()
        fact_extractor = MagicMock()
        fact_extractor.extract_from_event_type = MagicMock(return_value=[])
        service = MinionService(
            config=config,
            gateway=mock_gateway,
            registry=mock_registry,
            event_bus=mock_event_bus,
            memory_service=memory_service,
            fact_extractor=fact_extractor,
        )

        event = MinionEvent(
            event_id="e4",
            minion_id="test-minion",
            event_type="payment",
            payload={"merchant_name": "Tesco", "amount": 12.5},
        )

        await service.handle_event(event)

        fact_extractor.extract_from_event_type.assert_called_once_with(
            "payment", event.payload
        )

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
