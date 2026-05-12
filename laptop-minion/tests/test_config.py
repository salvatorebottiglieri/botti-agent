"""Tests for config module."""

import tempfile
from pathlib import Path

import pytest

from laptop_minion.config import (
    Config,
    SensorSettings,
    BatchSettings,
    load_config,
    save_config,
    get_config_path,
    get_state_path,
)


class TestConfig:
    """Tests for Config dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = Config()
        
        assert config.broker_url == "mqtt://localhost:1883"
        assert config.minion_id is None
        assert config.token == ""
        assert config.minion_type == "laptop"
        assert config.batch.max_size == 50
        assert config.batch.flush_interval == 30

    def test_config_sensors_default_enabled(self):
        """Test that sensors are enabled by default."""
        config = Config()
        
        assert config.sensors["screen_activity"].enabled is True
        assert config.sensors["application_focus"].enabled is True
        assert config.sensors["keyboard_activity"].enabled is True
        assert config.sensors["battery"].enabled is True
        assert config.sensors["network_status"].enabled is True

    def test_config_to_dict(self):
        """Test config serialization to dict."""
        config = Config()
        config_dict = config.to_dict()
        
        assert "broker_url" in config_dict
        assert "batch" in config_dict
        assert "sensors" in config_dict
        assert config_dict["batch"]["max_size"] == 50


class TestSensorSettings:
    """Tests for SensorSettings dataclass."""

    def test_default_sensor_settings(self):
        """Test default sensor settings."""
        settings = SensorSettings()
        
        assert settings.enabled is True
        assert settings.sampling_interval == 60
        assert settings.debounce_seconds is None

    def test_sensor_settings_custom_values(self):
        """Test custom sensor settings."""
        settings = SensorSettings(
            enabled=False,
            sampling_interval=120,
            debounce_seconds=10,
        )
        
        assert settings.enabled is False
        assert settings.sampling_interval == 120
        assert settings.debounce_seconds == 10

    def test_sensor_settings_to_dict(self):
        """Test sensor settings serialization."""
        settings = SensorSettings(enabled=True, sampling_interval=30)
        result = settings.to_dict()
        
        assert result["enabled"] is True
        assert result["sampling_interval"] == 30


class TestLoadConfig:
    """Tests for load_config function."""

    def test_load_config_cli_overrides(self):
        """Test that CLI arguments override config file."""
        config = load_config(
            broker_url="mqtt://test:1883",
            token="test-token",
            minion_id="test-id",
        )
        
        assert config.broker_url == "mqtt://test:1883"
        assert config.token == "test-token"
        assert config.minion_id == "test-id"

    def test_load_config_generates_minion_id(self):
        """Test that minion_id is generated if not provided."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = load_config(
                broker_url="mqtt://test:1883",
                token="test-token",
                state_dir=Path(tmpdir),
            )
            
            assert config.minion_id is not None
            assert len(config.minion_id) == 36  # UUID format

    def test_load_config_preserves_existing_minion_id(self):
        """Test that existing minion_id is preserved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            
            # Create initial config
            config1 = load_config(
                broker_url="mqtt://test:1883",
                token="test-token",
                state_dir=state_dir,
            )
            minion_id = config1.minion_id
            
            # Load again - should get same minion_id
            config2 = load_config(
                broker_url="mqtt://test:1883",
                token="test-token",
                state_dir=state_dir,
            )
            
            assert config2.minion_id == minion_id


class TestSaveConfig:
    """Tests for save_config function."""

    def test_save_and_load_config(self):
        """Test saving and loading configuration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config()
            config.broker_url = "mqtt://saved:1883"
            config.token = "saved-token"
            config.minion_id = "saved-id"
            
            config_path = Path(tmpdir) / "config.yaml"
            state_dir = Path(tmpdir)
            
            # Manually set path for test
            import yaml
            with open(config_path, "w") as f:
                yaml.safe_dump(config.to_dict(), f)
            
            # Load it back
            loaded = load_config(
                broker_url=None,
                token=None,
                minion_id=None,
                state_dir=state_dir,
                config_path=config_path,
            )
            
            assert loaded.broker_url == "mqtt://saved:1883"
            assert loaded.token == "saved-token"
            assert loaded.minion_id == "saved-id"
