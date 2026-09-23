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

from cortex.config.models import Settings

_UNRESOLVED_ENV_REF = re.compile(r"\$\{?\w+\}?")

#: YAML section -> slice field name, for the section keys whose name differs
#: from the field. ``config.yaml`` key names are a user-facing contract, so the
#: translation happens here and the file keeps its documented shape.
_YAML_KEY_TO_FIELD: dict[str, dict[str, str]] = {
    "database": {
        "url": "database_url",
        "pool_min_size": "db_pool_min_size",
        "pool_max_size": "db_pool_max_size",
        "pool_timeout": "db_pool_timeout",
    },
}

#: YAML sections that map onto a slice of the composed ``Settings``.
_SETTINGS_SECTIONS = ("database", "llm", "mqtt", "app", "logging", "trace", "learning")


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
            raise _UnresolvedEnvRefError(name)
        return os.environ[name]

    try:
        return _UNRESOLVED_ENV_REF.sub(_replace, value)
    except _UnresolvedEnvRefError:
        return None


class _UnresolvedEnvRefError(Exception):
    """Raised internally when a ${VAR} reference is not set in the environment."""


def _section_input(section: str, values: dict[str, Any]) -> dict[str, Any]:
    """Map a YAML section onto the field names of its settings slice.

    Values that resolved to ``None`` (an unset ``${VAR}`` reference) are
    dropped so the slice's environment variable or default applies instead.
    """
    aliases = _YAML_KEY_TO_FIELD.get(section, {})
    return {
        aliases.get(key, key): value
        for key, value in values.items()
        if value is not None
    }


def load_settings(config_path: Path | None = None) -> Settings:
    """
    Load settings from YAML file and environment variables.

    Priority (highest to lowest) — normative statement in ADR-0019:
    1. Environment variables
    2. YAML config file values
    3. ``.env`` file
    4. Default values in the settings models

    A YAML section that is absent (or a ``${VAR}`` reference whose variable is
    unset, which drops that key) leaves the field to its environment variable,
    its ``.env`` entry or its default.

    Args:
        config_path: Optional path to config YAML file.
                     If not provided, searches standard locations.

    Returns:
        Loaded Settings instance.

    Raises:
        ValidationError: If settings are invalid.
    """
    raw_yaml_config = load_yaml_config(config_path)
    yaml_config = {
        k: (
            {sk: _resolve_env_refs(sv) for sk, sv in v.items()}
            if isinstance(v, dict)
            else _resolve_env_refs(v)
        )
        for k, v in raw_yaml_config.items()
    }

    settings_data: dict[str, Any] = {
        section: _section_input(section, yaml_config[section])
        for section in _SETTINGS_SECTIONS
        if isinstance(yaml_config.get(section), dict)
    }

    return Settings(**settings_data)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Get cached settings instance.

    Uses lru_cache for singleton-like behavior within a process.
    Call this function to get the settings rather than instantiating directly.
    """
    return load_settings()
