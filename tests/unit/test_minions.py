"""Tests for the Minions module."""
import pytest
from datetime import datetime

from cortex.minions.models import (
    MinionInfo,
    MinionEvent,
    MinionEventBatch,
    MinionConfig,
    MinionCapabilities,
    MinionState,
    EventType,
    SensorType,
)
from cortex.minions.interfaces import MinionEventHandler, MinionRegistry, MinionGateway
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


class TestMinionEventHandler:
    """Tests for MinionEventHandler implementation."""

    @pytest.fixture
    def processor(self):
        """Create a MinionEventProcessor."""
        from cortex.minions.event_handler import MinionEventProcessor
        return MinionEventProcessor()

    @pytest.mark.asyncio
    async def test_handle_single_event(self, processor):
        """Test handling a single event."""
        event = MinionEvent.create(
            minion_id="phone-001",
            event_type=EventType.LOCATION_UPDATE,
            payload={"latitude": 37.7749},
        )

        await processor.handle_event(event)

        assert processor.processed_count == 1

    @pytest.mark.asyncio
    async def test_handle_batch(self, processor):
        """Test handling a batch of events."""
        events = [
            MinionEvent.create("phone-001", EventType.LOCATION_UPDATE, {}),
            MinionEvent.create("phone-001", EventType.ACTIVITY_DETECTED, {}),
            MinionEvent.create("phone-001", EventType.CALENDAR_EVENT, {}),
        ]
        batch = MinionEventBatch.create("phone-001", events)

        result = await processor.handle_batch(batch)

        assert len(result) == 3
        assert processor.processed_count == 3

    @pytest.mark.asyncio
    async def test_reset_stats(self, processor):
        """Test resetting statistics."""
        event = MinionEvent.create("phone-001", EventType.LOCATION_UPDATE, {})
        await processor.handle_event(event)

        assert processor.processed_count == 1

        processor.reset_stats()
        assert processor.processed_count == 0


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