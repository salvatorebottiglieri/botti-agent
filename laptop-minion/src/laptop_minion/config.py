"""Configuration loading and management for laptop-minion.

Supports loading from:
1. CLI arguments (overrides)
2. Config file (~/.config/laptop-minion/config.yaml)
3. Environment variables (CORTEX_BROKER_URL, CORTEX_TOKEN, etc.)
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ─────────────────────────────────────────────────────────────────────────────
# Dataclasses for typed config
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class SensorSettings:
    """Settings for a single sensor."""

    enabled: bool = True
    sampling_interval: int = 60  # seconds
    debounce_seconds: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "sampling_interval": self.sampling_interval}


@dataclass
class BatchSettings:
    """Settings for event batching."""

    max_size: int = 50
    flush_interval: int = 30  # seconds


@dataclass
class Config:
    """Full laptop-minion configuration."""

    broker_url: str = "mqtt://localhost:1883"
    minion_id: str | None = None
    minion_type: str = "laptop"
    token: str = ""
    batch: BatchSettings = field(default_factory=BatchSettings)
    sensors: dict[str, SensorSettings] = field(default_factory=lambda: {
        "screen_activity": SensorSettings(enabled=True, debounce_seconds=5),
        "application_focus": SensorSettings(enabled=True, debounce_seconds=10),
        "keyboard_activity": SensorSettings(enabled=True, sampling_interval=900),  # 15 min
        "battery": SensorSettings(enabled=True, debounce_seconds=30),
        "network_status": SensorSettings(enabled=True, debounce_seconds=10),
    })
    state_dir: Path = field(default_factory=lambda: Path("~/.config/laptop-minion").expanduser())

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for YAML serialization."""
        return {
            "broker_url": self.broker_url,
            "minion_id": self.minion_id,
            "minion_type": self.minion_type,
            "token": self.token,
            "batch": {
                "max_size": self.batch.max_size,
                "flush_interval": self.batch.flush_interval,
            },
            "sensors": {name: s.to_dict() for name, s in self.sensors.items()},
            "state_dir": str(self.state_dir),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Config file location
# ─────────────────────────────────────────────────────────────────────────────

CONFIG_FILE_NAME = "config.yaml"
STATE_FILE_NAME = "state.json"


def get_config_dir() -> Path:
    """Get the config directory, creating it if necessary."""
    config_dir = Path("~/.config/laptop-minion").expanduser()
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_state_dir() -> Path:
    """Alias for get_config_dir() - directory for state files."""
    return get_config_dir()


def get_config_path() -> Path:
    """Get the path to the config file."""
    return get_config_dir() / CONFIG_FILE_NAME


def get_state_path() -> Path:
    """Get the path to the state file (stores minion_id)."""
    return get_config_dir() / STATE_FILE_NAME


# ─────────────────────────────────────────────────────────────────────────────
# Config loading
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = Config()


def load_config(
    broker_url: str | None = None,
    token: str | None = None,
    minion_id: str | None = None,
    state_dir: Path | None = None,
    config_path: Path | None = None,
) -> Config:
    """Load configuration from file and CLI overrides.

    Priority (highest to lowest):
    1. CLI arguments
    2. Environment variables
    3. Config file
    4. Defaults
    """
    config = Config()

    # Load from config file
    if config_path is None:
        config_path = get_config_path()

    if config_path.exists():
        with open(config_path) as f:
            file_config = yaml.safe_load(f) or {}
        _apply_file_config(config, file_config)

    # Override with environment variables
    _apply_env_config(config)

    # Override with CLI arguments
    if broker_url is not None:
        config.broker_url = broker_url
    if token is not None:
        config.token = token
    if minion_id is not None:
        config.minion_id = minion_id
    if state_dir is not None:
        config.state_dir = state_dir

    # Ensure state_dir exists
    config.state_dir.mkdir(parents=True, exist_ok=True)

    # Generate minion_id if not set
    if config.minion_id is None:
        config.minion_id = _load_or_create_minion_id(config.state_dir)

    return config


def _apply_file_config(config: Config, file_config: dict[str, Any]) -> None:
    """Apply settings from config file dict."""
    if "broker_url" in file_config:
        config.broker_url = file_config["broker_url"]
    if "minion_id" in file_config:
        config.minion_id = file_config["minion_id"]
    if "token" in file_config:
        config.token = file_config["token"]

    if "batch" in file_config:
        batch = file_config["batch"]
        if "max_size" in batch:
            config.batch.max_size = batch["max_size"]
        if "flush_interval" in batch:
            config.batch.flush_interval = batch["flush_interval"]

    if "sensors" in file_config:
        for name, settings in file_config["sensors"].items():
            if isinstance(settings, dict):
                sensor = SensorSettings(
                    enabled=settings.get("enabled", True),
                    sampling_interval=settings.get("sampling_interval", 60),
                    debounce_seconds=settings.get("debounce_seconds"),
                )
                config.sensors[name] = sensor


def _apply_env_config(config: Config) -> None:
    """Apply settings from environment variables."""
    if "CORTEX_BROKER_URL" in os.environ:
        config.broker_url = os.environ["CORTEX_BROKER_URL"]
    if "CORTEX_TOKEN" in os.environ:
        config.token = os.environ["CORTEX_TOKEN"]
    if "CORTEX_MINION_ID" in os.environ:
        config.minion_id = os.environ["CORTEX_MINION_ID"]


def _load_or_create_minion_id(state_dir: Path) -> str:
    """Load minion_id from state file or create a new one."""
    state_path = state_dir / STATE_FILE_NAME
    if state_path.exists():
        with open(state_path) as f:
            state = yaml.safe_load(f) or {}
        if "minion_id" in state:
            return state["minion_id"]

    # Generate new UUID and save
    import uuid

    minion_id = str(uuid.uuid4())
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with open(state_path, "w") as f:
        yaml.safe_dump({"minion_id": minion_id}, f)
    return minion_id


def save_config(config: Config) -> None:
    """Save configuration to file."""
    config_path = get_config_path()
    with open(config_path, "w") as f:
        yaml.safe_dump(config.to_dict(), f, default_flow_style=False)


def create_default_config(path: Path | None = None) -> Path:
    """Create a default config file and return its path."""
    if path is None:
        path = get_config_path()
    if path.exists():
        return path
    save_config(DEFAULT_CONFIG)
    return path
