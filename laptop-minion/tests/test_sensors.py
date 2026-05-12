"""Tests for sensors module."""

from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from cortex_protocol.schemas import (
    ScreenActivityEvent,
    BatteryEvent,
    KeyboardActivityEvent,
    NetworkStatusEvent,
    NetworkType,
)

from laptop_minion.config import SensorSettings
from laptop_minion.sensors.base import BaseSensor, SensorEvent
from laptop_minion.sensors.battery import BatterySensor
from laptop_minion.sensors.network import NetworkSensor


class TestSensorSettings:
    """Tests for SensorSettings in sensor context."""

    def test_default_settings(self):
        """Test default sensor settings."""
        settings = SensorSettings()
        
        assert settings.enabled is True
        assert settings.sampling_interval == 60
        assert settings.debounce_seconds is None

    def test_disabled_sensor(self):
        """Test disabled sensor settings."""
        settings = SensorSettings(enabled=False)
        
        assert settings.enabled is False


class TestBatterySensor:
    """Tests for BatterySensor."""

    @pytest.fixture
    def mock_event_handler(self):
        """Create mock event handler."""
        return Mock()

    @pytest.fixture
    def sensor(self, mock_event_handler):
        """Create battery sensor with mock handler."""
        settings = SensorSettings(enabled=True)
        return BatterySensor(settings, mock_event_handler)

    def test_sensor_name(self, sensor):
        """Test sensor has correct name."""
        assert sensor.name == "battery"

    def test_sensor_enabled(self, sensor):
        """Test sensor is enabled by default."""
        assert sensor.enabled is True

    def test_disabled_sensor(self, mock_event_handler):
        """Test disabled sensor doesn't start."""
        settings = SensorSettings(enabled=False)
        sensor = BatterySensor(settings, mock_event_handler)
        
        sensor.start()
        
        # Should not call event handler for disabled sensor
        mock_event_handler.assert_not_called()

    def test_get_state_returns_dict(self, sensor):
        """Test that _get_current_state returns a dict."""
        state = sensor._get_current_state()
        
        assert isinstance(state, dict)
        assert "level" in state
        assert "is_charging" in state

    def test_should_emit_on_level_change(self, sensor):
        """Test sensor emits on significant level change."""
        # Use values that avoid floating point precision issues
        prev_state = {"level": 0.60, "is_charging": False}
        new_state = {"level": 0.54, "is_charging": False}
        
        # 6% change (> 5% threshold)
        should_emit = sensor._should_emit(prev_state, new_state)
        assert should_emit is True

    def test_should_emit_on_small_level_change(self, sensor):
        """Test sensor does NOT emit on small level change."""
        # Use values that avoid floating point precision issues
        prev_state = {"level": 0.60, "is_charging": False}
        new_state = {"level": 0.58, "is_charging": False}
        
        # 2% change (< 5% threshold) - no level change trigger
        # But note: if level goes below 0.20, low battery warning fires
        should_emit = sensor._should_emit(prev_state, new_state)
        # This should be False because 2% < 5% threshold
        assert should_emit is False

    def test_should_emit_on_charging_change(self, sensor):
        """Test sensor emits on charging state change."""
        prev_state = {"level": 0.5, "is_charging": False}
        new_state = {"level": 0.5, "is_charging": True}
        
        should_emit = sensor._should_emit(prev_state, new_state)
        assert should_emit is True

    def test_should_not_emit_on_small_change(self, sensor):
        """Test sensor doesn't emit on small changes."""
        prev_state = {"level": 0.5, "is_charging": False}
        new_state = {"level": 0.51, "is_charging": False}
        
        should_emit = sensor._should_emit(prev_state, new_state)
        assert should_emit is False

    def test_create_event(self, sensor):
        """Test event creation."""
        state = {
            "level": 0.75,
            "is_charging": True,
            "charging_type": "ac",
            "temperature": 30.0,
            "health": "good",
        }
        
        event = sensor._create_event(state)
        
        assert isinstance(event, BatteryEvent)
        assert event.type == "battery"
        assert event.payload.level == 0.75
        assert event.payload.is_charging is True

    def test_map_health(self, sensor):
        """Test health string mapping."""
        assert sensor._map_health("good") == "good"
        assert sensor._map_health("Normal") == "good"
        assert sensor._map_health("overheat") == "overheat"
        assert sensor._map_health("dead") == "dead"
        assert sensor._map_health("unknown") == "unspecified"


class TestNetworkSensor:
    """Tests for NetworkSensor."""

    @pytest.fixture
    def mock_event_handler(self):
        """Create mock event handler."""
        return Mock()

    @pytest.fixture
    def sensor(self, mock_event_handler):
        """Create network sensor with mock handler."""
        settings = SensorSettings(enabled=True)
        return NetworkSensor(settings, mock_event_handler)

    def test_sensor_name(self, sensor):
        """Test sensor has correct name."""
        assert sensor.name == "network_status"

    def test_sensor_enabled(self, sensor):
        """Test sensor is enabled by default."""
        assert sensor.enabled is True

    def test_get_state_returns_dict(self, sensor):
        """Test that _get_current_state returns a dict."""
        state = sensor._get_current_state()
        
        assert isinstance(state, dict)
        assert "connected" in state
        assert "network_type" in state

    def test_should_emit_on_connection_change(self, sensor):
        """Test sensor emits on connection state change."""
        prev_state = {"connected": False, "network_type": "none"}
        new_state = {"connected": True, "network_type": "wifi"}
        
        should_emit = sensor._should_emit(prev_state, new_state)
        assert should_emit is True

    def test_should_emit_on_signal_change(self, sensor):
        """Test sensor emits on significant signal change."""
        prev_state = {"connected": True, "network_type": "wifi", "signal_strength": 80}
        new_state = {"connected": True, "network_type": "wifi", "signal_strength": 60}
        
        should_emit = sensor._should_emit(prev_state, new_state)
        assert should_emit is True  # > 10% change

    def test_should_not_emit_on_small_signal_change(self, sensor):
        """Test sensor doesn't emit on small signal changes."""
        prev_state = {"connected": True, "network_type": "wifi", "signal_strength": 80}
        new_state = {"connected": True, "network_type": "wifi", "signal_strength": 78}
        
        should_emit = sensor._should_emit(prev_state, new_state)
        assert should_emit is False

    def test_create_event(self, sensor):
        """Test event creation."""
        state = {
            "connected": True,
            "network_type": "wifi",
            "ssid": "TestNetwork",
            "signal_strength": 85,
            "ip_address": "192.168.1.100",
            "vpn_active": False,
        }
        
        event = sensor._create_event(state)
        
        assert isinstance(event, NetworkStatusEvent)
        assert event.type == "network_status"
        assert event.payload.connected is True
        assert event.payload.ssid == "TestNetwork"
        assert event.payload.network_type == NetworkType.WIFI

    def test_map_network_type(self, sensor):
        """Test network type mapping."""
        assert sensor._map_network_type("wifi") == NetworkType.WIFI
        assert sensor._map_network_type("Wi-Fi") == NetworkType.WIFI
        assert sensor._map_network_type("ethernet") == NetworkType.ETHERNET
        assert sensor._map_network_type("cellular") == NetworkType.CELLULAR
        assert sensor._map_network_type("bluetooth") == NetworkType.BLUETOOTH
        assert sensor._map_network_type("unknown") == NetworkType.NONE


class TestSensorEvent:
    """Tests for SensorEvent wrapper."""

    def test_sensor_event_creation(self):
        """Test creating a sensor event."""
        battery_event = BatteryEvent(
            occurred_at=datetime.utcnow(),
            payload={"level": 0.5, "is_charging": False},
        )
        
        sensor_event = SensorEvent(
            sensor_name="battery",
            event=battery_event,
        )
        
        assert sensor_event.sensor_name == "battery"
        assert isinstance(sensor_event.event, BatteryEvent)
        assert sensor_event.timestamp is not None

    def test_sensor_event_auto_timestamp(self):
        """Test that sensor event auto-generates timestamp."""
        before = datetime.utcnow()
        
        battery_event = BatteryEvent(
            occurred_at=datetime.utcnow(),
            payload={"level": 0.5, "is_charging": False},
        )
        sensor_event = SensorEvent(
            sensor_name="battery",
            event=battery_event,
        )
        
        after = datetime.utcnow()
        
        assert before <= sensor_event.timestamp <= after
