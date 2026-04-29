"""
Configuration loader for Botticello.

Loads settings from YAML files and environment variables.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from cortex.config.models import Settings


def find_config_file() -> Path | None:
    """Find the config file in standard locations."""
    locations = [
        Path.cwd() / "config.yaml",
        Path.cwd() / "config.yml",
        Path.home() / ".config" / "botticello" / "config.yaml",
        Path("/etc/botticello/config.yaml"),
    ]

    for path in locations:
        if path.exists():
            return path

    return None


def load_yaml_config(config_path: Path | None = None) -> dict[str, Any]:
    """Load configuration from YAML file."""
    if config_path is None:
        config_path = find_config_file()

    if config_path is None:
        return {}

    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _flatten_dict(d: dict[str, Any], parent_key: str = "", sep: str = "_") -> dict[str, Any]:
    """Flatten nested dict for environment variable matching."""
    items: list[tuple[str, Any]] = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(_flatten_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)


def load_settings(config_path: Path | None = None) -> Settings:
    """
    Load settings from YAML file and environment variables.

    Priority (highest to lowest):
    1. Environment variables
    2. YAML config file
    3. Default values in Settings model

    Args:
        config_path: Optional path to config YAML file.
                     If not provided, searches standard locations.

    Returns:
        Loaded Settings instance.

    Raises:
        ValidationError: If settings are invalid.
    """
    # Load YAML config
    yaml_config = load_yaml_config(config_path)

    # Flatten YAML config for easier Pydantic field matching
    flattened = _flatten_dict(yaml_config)

    # Build kwargs for Settings, starting with YAML values
    # Environment variables will override these when Settings is instantiated
    settings_data: dict[str, Any] = {}

    # Map YAML keys to Pydantic field names
    yaml_to_pydantic = {
        "database_url": "database.url",
        "database_pool_min_size": "database.pool_min_size",
        "database_pool_max_size": "database.pool_max_size",
        "llm_provider": "llm.provider",
        "llm_api_key": "llm.api_key",
        "llm_model": "llm.model",
        "llm_base_url": "llm.base_url",
        "llm_timeout": "llm.timeout",
        "mqtt_broker_url": "mqtt.broker_url",
        "mqtt_username": "mqtt.username",
        "mqtt_password": "mqtt.password",
        "mqtt_client_id_prefix": "mqtt.client_id_prefix",
        "mqtt_keepalive": "mqtt.keepalive",
        "mqtt_reconnect_interval": "mqtt.reconnect_interval",
        "app_host": "app.host",
        "app_port": "app.port",
        "app_reload": "app.reload",
        "app_workers": "app.workers",
        "log_level": "logging.level",
        "log_format": "logging.format",
        "log_include_trace_id": "logging.include_trace_id",
    }

    # Apply flattened YAML values
    for yaml_key, value in flattened.items():
        if yaml_key in yaml_to_pydantic:
            # Map to nested field path
            path = yaml_to_pydantic[yaml_key]
            parts = path.split(".")
            target = settings_data
            for part in parts[:-1]:
                if part not in target:
                    target[part] = {}
                target = target[part]
            target[parts[-1]] = value

    try:
        return Settings(**settings_data)
    except ValidationError as e:
        # Re-raise with more context
        raise ValidationError.from_exception_data(
            title="Settings",
            line_errors=[
                {
                    "type": "value_error",
                    "msg": f"{le['loc']}: {le['msg']} (from config file: {config_path})"
                    if config_path else le['msg'],
                    "input": {},
                    "loc": le["loc"],
                }
                for le in e.errors()
            ]
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Get cached settings instance.

    Uses lru_cache for singleton-like behavior within a process.
    Call this function to get the settings rather than instantiating directly.
    """
    return load_settings()
