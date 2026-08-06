"""
Configuration loader for Botticello.

Loads settings from YAML files and environment variables.
"""

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from cortex.config.models import Settings

_UNRESOLVED_ENV_REF = re.compile(r"\$\{?\w+\}?")


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

    with open(config_path, encoding="utf-8") as f:
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


def _resolve_env_refs(value: Any) -> Any:
    """Expand ``${VAR}`` references in a YAML scalar.

    Returns the expanded value, or ``None`` when a referenced variable is
    unset so the caller can fall back to environment variables / defaults
    instead of passing a literal placeholder to Settings.
    """
    if not isinstance(value, str):
        return value

    def _replace(match: re.Match[str]) -> str:
        name = match.group(0).strip("${}")
        if name not in os.environ:
            raise _UnresolvedEnvRef(name)
        return os.environ[name]

    try:
        return _UNRESOLVED_ENV_REF.sub(_replace, value)
    except _UnresolvedEnvRef:
        return None


class _UnresolvedEnvRef(Exception):
    """Raised internally when a ${VAR} reference is not set in the environment."""


def load_settings(config_path: Path | None = None) -> Settings:
    """
    Load settings from YAML file and environment variables.

    Priority (highest to lowest):
    1. Environment variables
    2. YAML config file
    3. Default values in Settings model

    ``${VAR}`` references inside YAML scalars are resolved from the
    environment; values whose references are unset are dropped so the
    environment variable of the same name (or the field default) wins.

    Args:
        config_path: Optional path to config YAML file.
                     If not provided, searches standard locations.

    Returns:
        Loaded Settings instance.

    Raises:
        ValidationError: If settings are invalid.
    """
    # Load YAML config
    raw_yaml_config = load_yaml_config(config_path)
    yaml_config = {
        k: {sk: _resolve_env_refs(sv) for sk, sv in sv.items()} if isinstance(sv, dict) else _resolve_env_refs(sv)
        for k, sv in raw_yaml_config.items()
    }

    # Convert YAML to Pydantic-compatible nested dict
    settings_data: dict[str, Any] = {
        "database": {},
        "llm": {},
        "mqtt": {},
        "app": {},
        "log_level": None,
        "log_format": None,
        "log_include_trace_id": None,
    }

    # Map nested YAML config to flat settings fields
    if "database" in yaml_config:
        db = yaml_config["database"]
        if "url" in db:
            settings_data["database_url"] = db["url"]
        if "pool_min_size" in db:
            settings_data["db_pool_min_size"] = db["pool_min_size"]
        if "pool_max_size" in db:
            settings_data["db_pool_max_size"] = db["pool_max_size"]
        if "pool_timeout" in db:
            settings_data["db_pool_timeout"] = db["pool_timeout"]

    if "llm" in yaml_config:
        llm = yaml_config["llm"]
        if "provider" in llm:
            settings_data["llm_provider"] = llm["provider"]
        if "api_key" in llm:
            settings_data["llm_api_key"] = llm["api_key"]
        if "model" in llm:
            settings_data["llm_model"] = llm["model"]
        if "base_url" in llm:
            settings_data["llm_base_url"] = llm["base_url"]
        if "timeout" in llm:
            settings_data["llm_timeout"] = llm["timeout"]

    if "mqtt" in yaml_config:
        mqtt = yaml_config["mqtt"]
        if "broker_url" in mqtt:
            settings_data["mqtt_broker_url"] = mqtt["broker_url"]
        if "username" in mqtt:
            settings_data["mqtt_username"] = mqtt["username"]
        if "keepalive" in mqtt:
            settings_data["mqtt_keepalive"] = mqtt["keepalive"]
        if "reconnect_interval" in mqtt:
            settings_data["mqtt_reconnect_interval"] = mqtt["reconnect_interval"]

    if "app" in yaml_config:
        app = yaml_config["app"]
        if "host" in app:
            settings_data["app_host"] = app["host"]
        if "port" in app:
            settings_data["app_port"] = app["port"]
        if "reload" in app:
            settings_data["app_reload"] = app["reload"]
        if "workers" in app:
            settings_data["app_workers"] = app["workers"]

    if "logging" in yaml_config:
        log = yaml_config["logging"]
        if "level" in log:
            settings_data["log_level"] = log["level"]
        if "format" in log:
            settings_data["log_format"] = log["format"]
        if "include_trace_id" in log:
            settings_data["log_include_trace_id"] = log["include_trace_id"]

    # Remove None values
    settings_data = {k: v for k, v in settings_data.items() if v is not None}

    try:
        return Settings(**settings_data)
    except ValidationError:
        # Re-raise original error
        raise


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Get cached settings instance.

    Uses lru_cache for singleton-like behavior within a process.
    Call this function to get the settings rather than instantiating directly.
    """
    return load_settings()
