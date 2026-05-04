"""Tests for cortex_protocol Pydantic models and serialization."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from cortex_protocol import (
    ActivityEvent,
    # Enums
    ActivityType,
    AppCategory,
    ApplicationFocusEvent,
    AppUsageEvent,
    BatchConfig,
    BatteryEvent,
    CalendarEvent,
    CallLogEvent,
    CommandMessage,
    # Heartbeat and command
    HeartbeatMessage,
    KeyboardActivityEvent,
    # Events
    LocationEvent,
    LocationPayload,
    MerchantCategory,
    # Config
    MinionConfig,
    # Envelopes
    MinionEventBatch,
    MinionEventMetadata,
    # MQTT
    MQTTTopics,
    NetworkStatusEvent,
    NetworkType,
    PaymentEvent,
    PrivacyConfig,
    RefundEvent,
    ScreenActivityEvent,
    SensorConfig,
    UsageType,
)

# Fixture for timestamps used throughout tests
NOW = datetime(2026, 4, 28, 10, 0, 0, tzinfo=UTC)


class TestLocationEvent:
    """Tests for LocationEvent model."""

    def test_location_event_creation(self) -> None:
        """A LocationEvent can be created with required fields."""
        event = LocationEvent(
            occurred_at=NOW,
            payload=LocationPayload(
                latitude=41.9028,
                longitude=12.4964,
                altitude=21.0,
                accuracy=10.0,
                speed=0.0,
                heading=0.0,
                source="gps",
            ),
        )
        assert event.type == "location"
        assert event.payload.latitude == 41.9028
        assert event.payload.longitude == 12.4964

    def test_location_event_optional_fields(self) -> None:
        """Optional fields are correctly handled."""
        event = LocationEvent(
            occurred_at=NOW,
            payload=LocationPayload(
                latitude=41.9028,
                longitude=12.4964,
                accuracy=10.0,
            ),
        )
        assert event.payload.altitude is None
        assert event.payload.speed is None

    def test_location_event_speed_category(self) -> None:
        """Speed category can be set for derived motion state."""
        event = LocationEvent(
            occurred_at=NOW,
            payload=LocationPayload(
                latitude=41.9028,
                longitude=12.4964,
                accuracy=10.0,
                speed=5.5,
                speed_category="walking",
            ),
        )
        assert event.payload.speed_category == "walking"


class TestActivityEvent:
    """Tests for ActivityEvent model."""

    def test_activity_event_creation(self) -> None:
        """ActivityEvent with required fields."""
        event = ActivityEvent(
            occurred_at=NOW,
            payload={
                "activity_type": "walking",
                "confidence": 0.95,
                "start_time": NOW.isoformat(),
                "duration_seconds": 300,
            },
        )
        assert event.payload.activity_type == ActivityType.WALKING

    def test_activity_event_supporting_activities(self) -> None:
        """Supporting activities track multiple detected activities."""
        event = ActivityEvent(
            occurred_at=NOW,
            payload={
                "activity_type": "walking",
                "confidence": 0.75,
                "start_time": NOW.isoformat(),
                "duration_seconds": 300,
                "supporting_activities": ["still", "on_foot"],
                "supporting_confidences": [0.15, 0.1],
            },
        )
        assert len(event.payload.supporting_activities) == 2


class TestCalendarEvent:
    """Tests for CalendarEvent model."""

    def test_calendar_event_creation(self) -> None:
        """CalendarEvent with attendees and recurrence."""
        event = CalendarEvent(
            occurred_at=NOW,
            payload={
                "calendar_id": "cal_123",
                "event_id": "evt_456",
                "title": "Team Standup",
                "start_time": NOW.isoformat(),
                "end_time": "2026-04-28T09:15:00Z",
                "timezone": "America/New_York",
                "attendees": [
                    {"name": "Alice", "email": "alice@example.com", "status": "accepted"},
                    {"name": "Bob", "email": "bob@example.com", "status": "tentative"},
                ],
                "event_type": "meeting",
            },
        )
        assert event.payload.title == "Team Standup"
        assert len(event.payload.attendees) == 2

    def test_calendar_event_all_day(self) -> None:
        """All-day events don't include times."""
        event = CalendarEvent(
            occurred_at=NOW,
            payload={
                "calendar_id": "cal_123",
                "event_id": "evt_789",
                "title": "Company Holiday",
                "start_time": "2026-05-01T00:00:00Z",
                "end_time": "2026-05-02T00:00:00Z",
                "timezone": "UTC",
                "all_day": True,
            },
        )
        assert event.payload.all_day is True


class TestAppUsageEvent:
    """Tests for AppUsageEvent model."""

    def test_app_usage_event_creation(self) -> None:
        """AppUsageEvent captures app usage within time window."""
        event = AppUsageEvent(
            occurred_at=NOW,
            payload={
                "window_start": NOW.isoformat(),
                "window_end": "2026-04-28T10:15:00Z",
                "duration_seconds": 900,
                "package_name": "com.whatsapp",
                "app_name": "WhatsApp",
                "usage_type": "foreground",
                "foreground_duration_seconds": 720,
            },
        )
        assert event.payload.package_name == "com.whatsapp"
        assert event.payload.usage_type == UsageType.FOREGROUND


class TestCallLogEvent:
    """Tests for CallLogEvent model."""

    def test_call_log_incoming(self) -> None:
        """Incoming call with duration and contact."""
        event = CallLogEvent(
            occurred_at=NOW,
            payload={
                "direction": "incoming",
                "duration_seconds": 180,
                "contact_name": "John Doe",
                "answered": True,
            },
        )
        assert event.payload.direction == "incoming"
        assert event.payload.answered is True
        assert event.payload.phone_number is None  # Privacy: no raw numbers

    def test_call_log_missed(self) -> None:
        """Missed call with no contact."""
        event = CallLogEvent(
            occurred_at=NOW,
            payload={
                "direction": "missed",
                "duration_seconds": 0,
                "answered": False,
            },
        )
        assert event.payload.direction == "missed"


class TestPaymentEvent:
    """Tests for PaymentEvent model."""

    def test_payment_event_creation(self) -> None:
        """PaymentEvent with all transaction details."""
        event = PaymentEvent(
            occurred_at=NOW,
            payload={
                "transaction_id": "txn_abc123",
                "amount": "42.50",
                "currency": "EUR",
                "merchant_name": "Caffe Nero",
                "merchant_category": "cafe",
                "merchant_category_code": "5812",
                "merchant_city": "Rome",
                "card_last_four": "1234",
                "transaction_type": "purchase",
                "status": "completed",
            },
        )
        assert event.payload.amount == Decimal("42.50")
        assert event.payload.merchant_category == MerchantCategory.CAFE

    def test_payment_event_with_location(self) -> None:
        """Payment with optional GPS coordinates."""
        event = PaymentEvent(
            occurred_at=NOW,
            payload={
                "transaction_id": "txn_def456",
                "amount": "15.99",
                "currency": "USD",
                "merchant_name": "Amazon",
                "merchant_category": "shopping",
                "merchant_category_code": "5411",
                "latitude": 40.7128,
                "longitude": -74.0060,
                "card_last_four": "5678",
                "transaction_type": "purchase",
                "status": "completed",
            },
        )
        assert event.payload.latitude == 40.7128
        assert event.payload.longitude == -74.0060


class TestRefundEvent:
    """Tests for RefundEvent model."""

    def test_refund_event_creation(self) -> None:
        """RefundEvent references original transaction."""
        event = RefundEvent(
            occurred_at=NOW,
            payload={
                "refund_id": "ref_001",
                "original_transaction_id": "txn_abc123",
                "amount": "42.50",
                "currency": "EUR",
                "merchant_name": "Caffe Nero",
                "status": "completed",
                "initiated_at": NOW.isoformat(),
                "completed_at": "2026-04-28T12:30:00Z",
            },
        )
        assert event.payload.status == "completed"


class TestScreenActivityEvent:
    """Tests for ScreenActivityEvent model."""

    def test_screen_on_event(self) -> None:
        """Screen turned on event."""
        event = ScreenActivityEvent(
            occurred_at=NOW,
            payload={
                "event_type": "screen_on",
                "session_id": "session_123",
            },
        )
        assert event.payload.event_type == "screen_on"

    def test_active_window_changed(self) -> None:
        """Active window changed with app details."""
        event = ScreenActivityEvent(
            occurred_at=NOW,
            payload={
                "event_type": "active_window_changed",
                "window_title": "src/cortex/main.py - VS Code",
                "application_name": "Visual Studio Code",
                "application_bundle": "com.microsoft.VSCode",
                "session_id": "session_123",
            },
        )
        assert event.payload.application_name == "Visual Studio Code"

    def test_idle_started(self) -> None:
        """Idle detection started."""
        event = ScreenActivityEvent(
            occurred_at=NOW,
            payload={
                "event_type": "idle_started",
                "session_id": "session_123",
            },
        )
        assert event.payload.event_type == "idle_started"


class TestApplicationFocusEvent:
    """Tests for ApplicationFocusEvent model."""

    def test_application_focus_creation(self) -> None:
        """Application focus with category and duration."""
        event = ApplicationFocusEvent(
            occurred_at=NOW,
            payload={
                "application_name": "Visual Studio Code",
                "application_version": "1.85.0",
                "window_title": "src/main.py",
                "focus_duration_seconds": 3600,
                "app_category": "code_editor",
            },
        )
        assert event.payload.app_category == AppCategory.CODE_EDITOR


class TestKeyboardActivityEvent:
    """Tests for KeyboardActivityEvent model."""

    def test_keyboard_activity_creation(self) -> None:
        """Keyboard activity with aggregated metrics (no raw keystrokes)."""
        event = KeyboardActivityEvent(
            occurred_at=NOW,
            payload={
                "window_start": NOW.isoformat(),
                "window_end": "2026-04-28T10:15:00Z",
                "duration_seconds": 900,
                "keystrokes": 1500,
                "mouse_clicks": 120,
                "mouse_scroll_events": 45,
                "mouse_distance_px": 15000,
                "active_seconds": 600,
                "idle_seconds": 300,
            },
        )
        assert event.payload.keystrokes == 1500
        assert event.payload.active_seconds == 600

    def test_keyboard_activity_typing_speed(self) -> None:
        """Optional typing speed metric."""
        event = KeyboardActivityEvent(
            occurred_at=NOW,
            payload={
                "window_start": NOW.isoformat(),
                "window_end": "2026-04-28T10:15:00Z",
                "duration_seconds": 900,
                "keystrokes": 450,
                "mouse_clicks": 20,
                "mouse_scroll_events": 10,
                "mouse_distance_px": 5000,
                "active_seconds": 450,
                "idle_seconds": 450,
                "typing_speed_wpm": 65.0,
            },
        )
        assert event.payload.typing_speed_wpm == 65.0


class TestBatteryEvent:
    """Tests for BatteryEvent model."""

    def test_battery_event_creation(self) -> None:
        """Battery level with charging info."""
        event = BatteryEvent(
            occurred_at=NOW,
            payload={
                "level": 0.85,
                "is_charging": True,
                "charging_type": "ac",
                "temperature": 32.5,
                "health": "good",
            },
        )
        assert event.payload.level == 0.85
        assert event.payload.is_charging is True

    def test_battery_low_warning(self) -> None:
        """Low battery without charging."""
        event = BatteryEvent(
            occurred_at=NOW,
            payload={
                "level": 0.15,
                "is_charging": False,
                "health": "good",
            },
        )
        assert event.payload.level == 0.15


class TestNetworkStatusEvent:
    """Tests for NetworkStatusEvent model."""

    def test_network_status_wifi(self) -> None:
        """WiFi connection with signal strength."""
        event = NetworkStatusEvent(
            occurred_at=NOW,
            payload={
                "connected": True,
                "network_type": "wifi",
                "ssid": "HomeNetwork",
                "signal_strength": -55,
                "ip_address": "192.168.1.100",
                "vpn_active": False,
            },
        )
        assert event.payload.network_type == NetworkType.WIFI
        assert event.payload.ssid == "HomeNetwork"


class TestMinionEventBatch:
    """Tests for MinionEventBatch envelope."""

    def test_batch_creation(self) -> None:
        """MinionEventBatch wraps multiple events."""
        batch = MinionEventBatch(
            metadata=MinionEventMetadata(
                minion_id=uuid4(),
                minion_type="laptop",
                sequence=1234,
                batch_id=uuid4(),
                device_time=NOW,
            ),
            events=[
                ScreenActivityEvent(
                    occurred_at=NOW,
                    payload={
                        "event_type": "screen_on",
                        "session_id": "session_123",
                    },
                ),
                BatteryEvent(
                    occurred_at=NOW,
                    payload={
                        "level": 0.85,
                        "is_charging": True,
                    },
                ),
            ],
        )
        assert len(batch.events) == 2
        assert batch.metadata.sequence == 1234

    def test_batch_json_serialization(self) -> None:
        """Batch serializes to/from JSON correctly."""
        batch = MinionEventBatch(
            metadata=MinionEventMetadata(
                minion_id=uuid4(),
                minion_type="phone",
                sequence=1,
                batch_id=uuid4(),
                device_time=NOW,
            ),
            events=[
                LocationEvent(
                    occurred_at=NOW,
                    payload=LocationPayload(
                        latitude=41.9028,
                        longitude=12.4964,
                        accuracy=10.0,
                    ),
                ),
            ],
        )
        json_str = batch.model_dump_json()
        restored = MinionEventBatch.model_validate_json(json_str)
        assert restored.metadata.minion_type == "phone"

    def test_batch_with_occurred_at(self) -> None:
        """Events have occurred_at timestamp from device."""
        batch = MinionEventBatch(
            metadata=MinionEventMetadata(
                minion_id=uuid4(),
                minion_type="laptop",
                sequence=1,
                batch_id=uuid4(),
                device_time=NOW,
            ),
            events=[
                NetworkStatusEvent(
                    occurred_at=NOW,
                    payload={
                        "connected": True,
                        "network_type": "wifi",
                    },
                ),
            ],
        )
        assert batch.events[0].occurred_at == NOW


class TestHeartbeatMessage:
    """Tests for HeartbeatMessage schema."""

    def test_heartbeat_creation(self) -> None:
        """Heartbeat with all status fields."""
        heartbeat = HeartbeatMessage(
            minion_id=str(uuid4()),
            timestamp=NOW.isoformat(),
            status="healthy",
            battery_level=0.85,
            network_type="wifi",
            queue_size=0,
            last_sequence=1234,
            stats={
                "events_sent": 150,
                "events_failed": 0,
                "uptime_seconds": 3600,
            },
        )
        assert heartbeat.status == "healthy"
        assert heartbeat.battery_level == 0.85


class TestCommandMessage:
    """Tests for CommandMessage schema."""

    def test_config_command_creation(self) -> None:
        """Command message for config push."""
        command = CommandMessage(
            command_id=str(uuid4()),
            command="update_config",
            config={"sensors": {"location": {"enabled": True, "sampling_interval": 60}}},
        )
        assert command.command == "update_config"
        assert command.config is not None


class TestMinionConfig:
    """Tests for MinionConfig schema."""

    def test_minion_config_creation(self) -> None:
        """MinionConfig with sensors and batch settings."""
        config = MinionConfig(
            sensors={
                "location": SensorConfig(enabled=True, sampling_interval=60),
                "battery": SensorConfig(enabled=True, sampling_interval=300),
            },
            batch=BatchConfig(max_size=50, flush_interval=30),
            privacy=PrivacyConfig(exclude_apps=["com.instagram"]),
        )
        assert config.sensors["location"].enabled is True
        assert config.batch.max_size == 50

    def test_minion_config_yaml_serialization(self) -> None:
        """MinionConfig can be serialized to/from YAML."""
        import yaml

        config = MinionConfig(
            sensors={
                "screen_activity": SensorConfig(
                    enabled=True,
                    sampling_interval=5,
                    significant_change=None,
                    debounce_seconds=2,
                ),
            },
            batch=BatchConfig(max_size=50, flush_interval=30),
        )
        yaml_str = yaml.dump(config.model_dump())
        restored = MinionConfig.model_validate(yaml.safe_load(yaml_str))
        assert restored.batch.flush_interval == 30

    def test_sensor_config_all_fields(self) -> None:
        """SensorConfig with all optional fields."""
        sensor = SensorConfig(
            enabled=True,
            sampling_interval=30,
            significant_change=50.0,  # meters
            debounce_seconds=5,
        )
        assert sensor.significant_change == 50.0


class TestMQTTTopics:
    """Tests for MQTT topic constants."""

    def test_topic_structure(self) -> None:
        """Topic constants follow expected structure."""
        minion_id = "abc123"
        assert MQTTTopics.events(minion_id) == f"cortex/minions/{minion_id}/events"
        assert MQTTTopics.heartbeat(minion_id) == f"cortex/minions/{minion_id}/heartbeat"
        assert MQTTTopics.register(minion_id) == f"cortex/minions/{minion_id}/register"

    def test_command_topics(self) -> None:
        """Command topics use wildcard for sub-topics."""
        minion_id = "abc123"
        assert MQTTTopics.commands(minion_id) == f"cortex/minions/{minion_id}/commands/#"
        assert MQTTTopics.command_config(minion_id) == f"cortex/minions/{minion_id}/commands/config"


class TestEventDiscrimination:
    """Tests for event union discrimination."""

    def test_all_events_have_type_field(self) -> None:
        """All events must have a type discriminator field."""
        event_instances = [
            LocationEvent(
                occurred_at=NOW, payload={"latitude": 0.0, "longitude": 0.0, "accuracy": 1.0}
            ),
            ActivityEvent(
                occurred_at=NOW,
                payload={
                    "activity_type": "still",
                    "confidence": 1.0,
                    "start_time": NOW.isoformat(),
                    "duration_seconds": 0,
                },
            ),
            CalendarEvent(
                occurred_at=NOW,
                payload={
                    "calendar_id": "x",
                    "event_id": "x",
                    "title": "x",
                    "start_time": NOW.isoformat(),
                    "end_time": NOW.isoformat(),
                    "timezone": "UTC",
                },
            ),
            AppUsageEvent(
                occurred_at=NOW,
                payload={
                    "window_start": NOW.isoformat(),
                    "window_end": NOW.isoformat(),
                    "duration_seconds": 0,
                    "package_name": "x",
                    "usage_type": "foreground",
                    "foreground_duration_seconds": 0,
                },
            ),
            CallLogEvent(
                occurred_at=NOW,
                payload={"direction": "missed", "duration_seconds": 0, "answered": False},
            ),
            PaymentEvent(
                occurred_at=NOW,
                payload={
                    "transaction_id": "x",
                    "amount": "0",
                    "currency": "USD",
                    "merchant_name": "x",
                    "merchant_category": "other",
                    "merchant_category_code": "0000",
                    "card_last_four": "0000",
                    "transaction_type": "purchase",
                    "status": "completed",
                },
            ),
            RefundEvent(
                occurred_at=NOW,
                payload={
                    "refund_id": "x",
                    "original_transaction_id": "x",
                    "amount": "0",
                    "currency": "USD",
                    "status": "initiated",
                    "initiated_at": NOW.isoformat(),
                },
            ),
            ScreenActivityEvent(
                occurred_at=NOW, payload={"event_type": "screen_on", "session_id": "x"}
            ),
            ApplicationFocusEvent(
                occurred_at=NOW,
                payload={
                    "application_name": "x",
                    "focus_duration_seconds": 0,
                    "app_category": "other",
                },
            ),
            KeyboardActivityEvent(
                occurred_at=NOW,
                payload={
                    "window_start": NOW.isoformat(),
                    "window_end": NOW.isoformat(),
                    "duration_seconds": 0,
                    "keystrokes": 0,
                    "mouse_clicks": 0,
                    "mouse_scroll_events": 0,
                    "mouse_distance_px": 0,
                    "active_seconds": 0,
                    "idle_seconds": 0,
                },
            ),
            BatteryEvent(occurred_at=NOW, payload={"level": 1.0, "is_charging": False}),
            NetworkStatusEvent(
                occurred_at=NOW, payload={"connected": True, "network_type": "wifi"}
            ),
        ]
        for instance in event_instances:
            assert hasattr(instance, "type")
            assert instance.type is not None

    def test_event_type_values(self) -> None:
        """Each event has expected type string."""
        assert (
            LocationEvent(
                occurred_at=NOW, payload={"latitude": 0.0, "longitude": 0.0, "accuracy": 1.0}
            ).type
            == "location"
        )
        assert (
            ActivityEvent(
                occurred_at=NOW,
                payload={
                    "activity_type": "still",
                    "confidence": 1.0,
                    "start_time": NOW.isoformat(),
                    "duration_seconds": 0,
                },
            ).type
            == "activity"
        )
        assert (
            BatteryEvent(occurred_at=NOW, payload={"level": 1.0, "is_charging": False}).type
            == "battery"
        )
        assert (
            NetworkStatusEvent(
                occurred_at=NOW, payload={"connected": True, "network_type": "wifi"}
            ).type
            == "network_status"
        )


class TestValidationEdgeCases:
    """Tests for validation edge cases."""

    def test_latitude_range(self) -> None:
        """Latitude must be between -90 and 90."""
        with pytest.raises(ValidationError):
            LocationEvent(
                occurred_at=NOW,
                payload={
                    "latitude": 100.0,  # Invalid: > 90
                    "longitude": 12.4964,
                    "accuracy": 10.0,
                },
            )

    def test_longitude_range(self) -> None:
        """Longitude must be between -180 and 180."""
        with pytest.raises(ValidationError):
            LocationEvent(
                occurred_at=NOW,
                payload={
                    "latitude": 41.9028,
                    "longitude": 200.0,  # Invalid: > 180
                    "accuracy": 10.0,
                },
            )

    def test_battery_level_range(self) -> None:
        """Battery level must be between 0 and 1."""
        with pytest.raises(ValidationError):
            BatteryEvent(
                occurred_at=NOW,
                payload={
                    "level": 1.5,  # Invalid: > 1.0
                    "is_charging": False,
                },
            )

    def test_heading_range(self) -> None:
        """Heading must be between 0 and 360."""
        with pytest.raises(ValidationError):
            LocationEvent(
                occurred_at=NOW,
                payload={
                    "latitude": 41.9028,
                    "longitude": 12.4964,
                    "accuracy": 10.0,
                    "heading": 400.0,  # Invalid: > 360
                },
            )
