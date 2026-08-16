"""Tests for the Minions module."""
from datetime import UTC, datetime

import pytest

from cortex.minions.models import (
    EventType,
    MinionConfig,
    MinionEvent,
    MinionEventBatch,
    MinionInfo,
    MinionState,
    SensorType,
)
from cortex.minions.registry import InMemoryMinionRegistry


class TestMinionModels:
    """Tests for minion data models."""

    def test_minion_info_creation(self):
        """Test creating MinionInfo."""
        info = MinionInfo(
            minion_id="phone-001",
            name="My Phone",
            device_type="android",
            capabilities={"sensors": [SensorType.LOCATION, SensorType.ACTIVITY]},
        )

        assert info.minion_id == "phone-001"
        assert info.name == "My Phone"
        assert info.device_type == "android"
        assert info.state == MinionState.OFFLINE
        assert info.last_heartbeat is None

    def test_minion_info_to_dict(self):
        """Test MinionInfo serialization."""
        info = MinionInfo(
            minion_id="phone-001",
            name="My Phone",
            device_type="android",
            capabilities={},
        )

        data = info.to_dict()
        assert data["minion_id"] == "phone-001"
        assert data["name"] == "My Phone"
        assert data["state"] == "offline"

    def test_minion_info_from_dict(self):
        """Test MinionInfo deserialization."""
        data = {
            "minion_id": "phone-002",
            "name": "Tablet",
            "device_type": "ipad",
            "capabilities": {"sensors": ["location"]},
            "state": "online",
            "last_heartbeat": None,
            "created_at": "2026-04-30T10:00:00",
            "metadata": {},
        }

        info = MinionInfo.from_dict(data)
        assert info.minion_id == "phone-002"
        assert info.name == "Tablet"
        assert info.state == MinionState.ONLINE

    def test_minion_event_creation(self):
        """Test creating a minion event."""
        event = MinionEvent.create(
            minion_id="phone-001",
            event_type=EventType.LOCATION_UPDATE,
            payload={"latitude": 37.7749, "longitude": -122.4194},
        )

        assert event.minion_id == "phone-001"
        assert event.event_type == EventType.LOCATION_UPDATE
        assert event.payload["latitude"] == 37.7749
        assert event.event_id is not None

    def test_minion_event_to_dict(self):
        """Test MinionEvent serialization."""
        event = MinionEvent.create(
            minion_id="phone-001",
            event_type=EventType.LOCATION_UPDATE,
            payload={"latitude": 37.7749},
        )

        data = event.to_dict()
        assert data["minion_id"] == "phone-001"
        assert data["event_type"] == "location.update"
        assert data["payload"]["latitude"] == 37.7749

    def test_minion_event_batch_creation(self):
        """Test creating a batch of events."""
        event1 = MinionEvent.create(
            minion_id="phone-001",
            event_type=EventType.LOCATION_UPDATE,
            payload={},
        )
        event2 = MinionEvent.create(
            minion_id="phone-001",
            event_type=EventType.ACTIVITY_DETECTED,
            payload={},
        )

        batch = MinionEventBatch.create("phone-001", [event1, event2])

        assert batch.minion_id == "phone-001"
        assert len(batch.events) == 2
        assert batch.batch_id is not None

    def test_minion_event_batch_to_dict(self):
        """Test MinionEventBatch serialization."""
        event = MinionEvent.create(
            minion_id="phone-001",
            event_type=EventType.LOCATION_UPDATE,
            payload={},
        )
        batch = MinionEventBatch.create("phone-001", [event])

        data = batch.to_dict()
        assert data["minion_id"] == "phone-001"
        assert len(data["events"]) == 1

    def test_minion_config_defaults(self):
        """Test MinionConfig defaults."""
        config = MinionConfig(
            broker_url="mqtt://localhost",
            minion_id="test-minion",
            minion_name="Test",
            device_type="test",
        )

        assert config.port == 1883
        assert config.keepalive == 60
        assert config.qos == 1
        assert config.topics is not None
        assert len(config.topics) == 2

    def test_minion_config_custom_topics(self):
        """Test MinionConfig with custom topics."""
        config = MinionConfig(
            broker_url="mqtt://localhost",
            minion_id="test-minion",
            minion_name="Test",
            device_type="test",
            topics=["custom/topic/1", "custom/topic/2"],
        )

        assert len(config.topics) == 2
        assert config.topics[0] == "custom/topic/1"


class TestInMemoryMinionRegistry:
    """Tests for InMemoryMinionRegistry."""

    @pytest.fixture
    def registry(self) -> InMemoryMinionRegistry:
        """Create a fresh registry."""
        return InMemoryMinionRegistry()

    @pytest.fixture
    def sample_info(self) -> MinionInfo:
        """Create sample minion info."""
        return MinionInfo(
            minion_id="phone-001",
            name="My Phone",
            device_type="android",
            capabilities={"sensors": [SensorType.LOCATION]},
        )

    @pytest.mark.asyncio
    async def test_register_minion(self, registry, sample_info):
        """Test registering a minion."""
        await registry.register("phone-001", sample_info)

        result = await registry.get("phone-001")
        assert result is not None
        assert result.name == "My Phone"
        assert result.state == MinionState.ONLINE

    @pytest.mark.asyncio
    async def test_unregister_minion(self, registry, sample_info):
        """Test unregistering a minion."""
        await registry.register("phone-001", sample_info)
        await registry.unregister("phone-001")

        result = await registry.get("phone-001")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_all(self, registry):
        """Test listing all minions."""
        info1 = MinionInfo("phone-001", "Phone 1", "android", {})
        info2 = MinionInfo("phone-002", "Phone 2", "iphone", {})

        await registry.register("phone-001", info1)
        await registry.register("phone-002", info2)

        all_minions = await registry.list_all()
        assert len(all_minions) == 2

    @pytest.mark.asyncio
    async def test_list_active(self, registry):
        """Test listing active minions."""
        info1 = MinionInfo("phone-001", "Phone 1", "android", {})
        info2 = MinionInfo("phone-002", "Phone 2", "iphone", {})

        await registry.register("phone-001", info1)
        await registry.register("phone-002", info2)

        # Update one to away
        await registry.update_state("phone-001", "away")

        active = await registry.list_active()
        assert len(active) == 1
        assert active[0].minion_id == "phone-002"

    @pytest.mark.asyncio
    async def test_heartbeat(self, registry, sample_info):
        """Test heartbeat updates for registered minion."""
        # First register the minion
        await registry.register("phone-001", sample_info)

        # Heartbeat should update last_heartbeat
        await registry.heartbeat("phone-001")
        info = await registry.get("phone-001")
        assert info is not None
        assert info.last_heartbeat is not None

    @pytest.mark.asyncio
    async def test_update_state(self, registry, sample_info):
        """Test updating minion state."""
        await registry.register("phone-001", sample_info)

        await registry.update_state("phone-001", "away")
        info = await registry.get("phone-001")
        assert info.state == MinionState.AWAY

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, registry):
        """Test getting a nonexistent minion."""
        result = await registry.get("nonexistent")
        assert result is None


class TestMinionEventProcessor:
    """Tests for MinionEventProcessor emitting to event bus."""

    @pytest.mark.asyncio
    async def test_handle_location_event_emits_to_event_bus(self):
        """
        RED: When a location event is processed, it should emit a BaseEvent
        to the event bus with type 'minion.location'.
        """
        from cortex.events.bus import EventBus
        from cortex.minions.event_handler import MinionEventProcessor

        # Create event bus
        bus = EventBus()
        await bus.start()


        # Create processor with event bus
        processor = MinionEventProcessor(event_bus=bus)

        # Create a location event
        event = MinionEvent.create(
            minion_id="phone-001",
            event_type=EventType.LOCATION_UPDATE,
            payload={"latitude": 37.7749, "longitude": -122.4194},
        )

        # Track events published to bus
        published_events = []
        async def capture_handler(e):
            published_events.append(e)

        await bus.subscribe("minion.location", capture_handler)

        # Process the event
        await processor.handle_event(event)

        # Assert event was published
        assert len(published_events) == 1
        assert published_events[0].type == "minion.location"
        assert published_events[0].payload["minion_id"] == "phone-001"
        # Original payload is nested under 'payload' key
        assert published_events[0].payload["payload"]["latitude"] == 37.7749
        assert published_events[0].payload["payload"]["longitude"] == -122.4194

    @pytest.mark.asyncio
    async def test_handle_battery_event_emits_to_event_bus(self):
        """
        RED: Battery events should emit 'minion.battery' to the event bus.
        """
        from cortex.events.bus import EventBus
        from cortex.minions.event_handler import MinionEventProcessor

        bus = EventBus()
        await bus.start()
        processor = MinionEventProcessor(event_bus=bus)

        event = MinionEvent.create(
            minion_id="laptop-001",
            event_type=EventType.BATTERY_LEVEL,
            payload={"level": 0.75, "is_charging": True},
        )

        published_events = []
        async def capture_handler(e):
            published_events.append(e)

        await bus.subscribe("minion.battery", capture_handler)

        await processor.handle_event(event)

        assert len(published_events) == 1
        assert published_events[0].type == "minion.battery"
        assert published_events[0].payload["payload"]["level"] == 0.75
        assert published_events[0].payload["payload"]["is_charging"] is True

        if bus.is_running:
            await bus.stop()


class TestMinionInterfaces:
    """Tests to verify interface contracts."""

    def test_minion_info_has_required_fields(self):
        """Test MinionInfo has all required fields."""
        info = MinionInfo(
            minion_id="test",
            name="Test",
            device_type="test",
            capabilities={},
        )

        assert hasattr(info, "minion_id")
        assert hasattr(info, "name")
        assert hasattr(info, "device_type")
        assert hasattr(info, "capabilities")
        assert hasattr(info, "state")
        assert hasattr(info, "last_heartbeat")

    def test_minion_event_has_required_fields(self):
        """Test MinionEvent has all required fields."""
        event = MinionEvent.create(
            minion_id="test",
            event_type=EventType.LOCATION_UPDATE,
            payload={},
        )


        assert hasattr(event, "event_id")
        assert hasattr(event, "minion_id")
        assert hasattr(event, "event_type")
        assert hasattr(event, "payload")
        assert hasattr(event, "timestamp")

    def test_minion_config_has_broker_url(self):
        """Test MinionConfig has broker_url."""
        config = MinionConfig(
            broker_url="mqtt://localhost",
            minion_id="test",
            minion_name="Test",
            device_type="test",
        )

        assert config.broker_url == "mqtt://localhost"

    def test_sensor_type_values(self):
        """Test SensorType enum values."""
        assert SensorType.LOCATION.value == "location"
        assert SensorType.CALENDAR.value == "calendar"
        assert SensorType.ACTIVITY.value == "activity"
        assert SensorType.APP_USAGE.value == "app_usage"
        assert SensorType.BATTERY.value == "battery"

    def test_event_type_values(self):
        """Test EventType enum values."""
        assert EventType.REGISTERED.value == "minion.registered"
        assert EventType.HEARTBEAT.value == "minion.heartbeat"
        assert EventType.LOCATION_UPDATE.value == "location.update"

    def test_payment_and_call_log_event_types(self):
        """Test payment/call_log event types construct via MQTT path."""
        event = MinionEvent.create(
            minion_id="m",
            event_type="payment",
            payload={},
        )
        assert event.event_type == EventType.PAYMENT
        assert EventType("payment") is EventType.PAYMENT
        assert EventType("call_log") is EventType.CALL_LOG


class TestPostgresMinionRegistry:
    """Tests for Postgres-backed MinionRegistry."""

    @pytest.mark.asyncio
    async def test_register_minion_persists_to_db(self):
        """
        RED: When a minion is registered, it should persist to the minions table.
        """
        from unittest.mock import AsyncMock, MagicMock

        from cortex.minions.models import MinionInfo, MinionState
        from cortex.minions.registry import PostgresMinionRegistry

        # Create mock pool
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        registry = PostgresMinionRegistry(pool=mock_pool)

        minion_info = MinionInfo(
            minion_id="phone-001",
            name="My Phone",
            device_type="android",
            capabilities={},
            state=MinionState.ONLINE,
        )


        await registry.register("phone-001", minion_info)


        # Verify INSERT was called
        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args
        assert "INSERT INTO minions" in call_args[0][0]


    @pytest.mark.asyncio
    async def test_heartbeat_updates_last_heartbeat_at(self):
        """
        RED: Heartbeat should update last_heartbeat_at in the database.
        """
        from unittest.mock import AsyncMock, MagicMock

        from cortex.minions.registry import PostgresMinionRegistry


        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        registry = PostgresMinionRegistry(pool=mock_pool)

        await registry.heartbeat("phone-001")

        # Verify UPDATE was called with last_heartbeat_at
        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args
        assert "UPDATE minions SET last_heartbeat_at" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_get_minion_returns_info(self):
        """
        RED: Getting a minion should return MinionInfo from the database.
        """
        from unittest.mock import AsyncMock, MagicMock

        from cortex.minions.registry import PostgresMinionRegistry

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        # Mock query result - fetchrow returns a row object
        mock_row = MagicMock()
        mock_row.keys.return_value = ["minion_id", "name", "device_type", "state", "last_heartbeat_at", "capabilities", "metadata"]
        mock_row.__getitem__ = lambda self, k: {
            "minion_id": "phone-001",
            "name": "My Phone",
            "device_type": "android",
            "state": "online",
            "last_heartbeat_at": datetime.now(UTC),
            "capabilities": {},
            "metadata": {},
        }.get(k)
        # Mock fetchrow directly on the connection
        mock_conn.fetchrow = AsyncMock(return_value=mock_row)

        registry = PostgresMinionRegistry(pool=mock_pool)
        result = await registry.get("phone-001")


        assert result is not None
        assert result.minion_id == "phone-001"
        assert result.name == "My Phone"



class TestMinionServiceSequenceGap:
    """Tests for sequence gap detection in minion events."""

    @pytest.mark.asyncio
    async def test_sequence_gap_detected_and_logged(self, caplog):
        """
        GREEN: When events arrive with non-contiguous sequence numbers,
        a warning should be logged. Using caplog for log capture.
        """
        import logging
        from unittest.mock import AsyncMock, MagicMock

        from cortex.events.bus import EventBus
        from cortex.minions.models import EventType, MinionConfig, MinionEvent
        from cortex.services.minion_service import MinionService

        bus = EventBus()
        await bus.start()

        try:
            # Create mocks
            mock_gateway = MagicMock()
            mock_gateway.connect = AsyncMock()
            mock_gateway.disconnect = AsyncMock()
            mock_gateway.subscribe = AsyncMock()
            mock_gateway.is_connected.return_value = True

            mock_registry = MagicMock()
            mock_registry.register = AsyncMock()
            mock_registry.heartbeat = AsyncMock()
            mock_registry.update_state = AsyncMock()
            mock_registry.get = AsyncMock(return_value=None)

            service = MinionService(
                config=MinionConfig(
                    broker_url="mqtt://localhost",
                    minion_id="test",
                    minion_name="Test",
                    device_type="test",
                ),
                gateway=mock_gateway,
                registry=mock_registry,
                event_bus=bus,
                fact_store=None,
            )

            # Set up service and track sequences via events
            # First establish sequence=1
            event1 = MinionEvent.create(
                minion_id="phone-001",
                event_type=EventType.LOCATION_UPDATE,
                payload={"latitude": 37.0, "longitude": -122.0},
            )
            event1.sequence_number = 1
            await service.handle_event(event1)

            # Now send event with seq=3 (gap of 2 from seq=1)
            event2 = MinionEvent.create(
                minion_id="phone-001",
                event_type=EventType.LOCATION_UPDATE,
                payload={"latitude": 37.1, "longitude": -122.1},
            )
            event2.sequence_number = 3  # Gap from last (1) to (3)

            with caplog.at_level(logging.WARNING):
                await service.handle_event(event2)

            # Check that warning was logged about sequence gap
            assert any("sequence" in record.message.lower() or "gap" in record.message.lower()
                      for record in caplog.records), \
                f"Should log warning for sequence gap. Got: {[r.message for r in caplog.records]}"
        finally:
            if bus.is_running:
                await bus.stop()
