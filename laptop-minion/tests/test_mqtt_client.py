"""Tests for MQTT client module."""

from datetime import datetime
from uuid import uuid4

import pytest

from laptop_minion.config import Config, SensorSettings
from laptop_minion.mqtt_client import (
    CortexMQTTClient,
    ConnectionStatus,
    EventStats,
    calculate_backoff,
    BACKOFF_BASE_SECONDS,
    BACKOFF_MAX_SECONDS,
)


class TestCalculateBackoff:
    """Tests for backoff calculation."""

    def test_backoff_increases_with_attempts(self):
        """Test that backoff increases with attempt number."""
        backoff_0 = calculate_backoff(0)
        backoff_1 = calculate_backoff(1)
        backoff_2 = calculate_backoff(2)
        
        assert backoff_0 <= backoff_1 <= backoff_2

    def test_backoff_uses_base_values(self):
        """Test that backoff uses predefined base values."""
        for i, base in enumerate(BACKOFF_BASE_SECONDS):
            backoff = calculate_backoff(i)
            # Should be close to base value (with jitter)
            assert base * 0.8 <= backoff <= base * 1.2

    def test_backoff_caps_at_max(self):
        """Test that backoff caps at maximum value."""
        for _ in range(10):
            backoff = calculate_backoff(100)  # Very high attempt
            assert backoff <= BACKOFF_MAX_SECONDS * 1.2  # Allow for jitter

    def test_backoff_has_jitter(self):
        """Test that backoff includes jitter."""
        backoffs = [calculate_backoff(0) for _ in range(10)]
        
        # With jitter, we should see variation
        assert len(set(round(b, 1) for b in backoffs)) > 1


class TestConnectionStatus:
    """Tests for ConnectionStatus dataclass."""

    def test_default_status(self):
        """Test default connection status."""
        status = ConnectionStatus()
        
        assert status.connected is False
        assert status.connecting is False
        assert status.disconnected_at is None
        assert status.reconnect_attempts == 0
        assert status.last_connected_at is None

    def test_status_copy(self):
        """Test that status can be copied."""
        status = ConnectionStatus(
            connected=True,
            reconnect_attempts=5,
        )
        
        # Create a copy (simulating property return)
        copy = ConnectionStatus(
            connected=status.connected,
            connecting=status.connecting,
            disconnected_at=status.disconnected_at,
            reconnect_attempts=status.reconnect_attempts,
            last_connected_at=status.last_connected_at,
        )
        
        assert copy.connected == status.connected
        assert copy.reconnect_attempts == status.reconnect_attempts


class TestEventStats:
    """Tests for EventStats dataclass."""

    def test_default_stats(self):
        """Test default event statistics."""
        stats = EventStats()
        
        assert stats.sent == 0
        assert stats.queued == 0
        assert stats.flushed == 0
        assert stats.failed == 0

    def test_stats_update(self):
        """Test updating event statistics."""
        stats = EventStats()
        
        stats.sent += 10
        stats.queued += 5
        stats.flushed += 3
        
        assert stats.sent == 10
        assert stats.queued == 5
        assert stats.flushed == 3


class TestCortexMQTTClient:
    """Tests for CortexMQTTClient."""

    @pytest.fixture
    def config(self):
        """Create test configuration."""
        return Config(
            broker_url="mqtt://localhost:1883",
            minion_id=str(uuid4()),
            token="test-token",
        )

    def test_client_initialization(self, config):
        """Test client initializes correctly."""
        client = CortexMQTTClient(config)
        
        assert client.status.connected is False
        assert client.status.connecting is False
        assert client.queue_size == 0

    def test_client_stats_initial(self, config):
        """Test client initial stats."""
        client = CortexMQTTClient(config)
        stats = client.stats
        
        assert stats.sent == 0
        assert stats.queued == 0
        assert stats.flushed == 0

    def test_client_uses_config_values(self, config):
        """Test client uses config for batching."""
        config.batch.max_size = 100
        config.batch.flush_interval = 60
        
        client = CortexMQTTClient(config)
        
        assert client._batch_config.max_size == 100
        assert client._batch_config.flush_interval == 60

    def test_client_with_callbacks(self, config):
        """Test client with connection callbacks."""
        connect_called = []
        disconnect_called = []
        
        def on_connect():
            connect_called.append(True)
        
        def on_disconnect():
            disconnect_called.append(True)
        
        client = CortexMQTTClient(
            config,
            on_connect=on_connect,
            on_disconnect=on_disconnect,
        )
        
        # Simulate callbacks
        client._on_connect()
        client._on_disconnect()
        
        assert len(connect_called) == 1
        assert len(disconnect_called) == 1

    def test_client_id_contains_minion_id(self, config):
        """Test that client ID includes minion ID."""
        client = CortexMQTTClient(config)
        
        assert config.minion_id in client._client_id
        assert "laptop-minion" in client._client_id

    def test_client_disconnect(self, config):
        """Test client disconnect."""
        client = CortexMQTTClient(config)
        
        # Should not raise
        client.disconnect()
        
        # Status should still be disconnected
        assert client.status.connected is False
