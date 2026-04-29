"""
Configuration system for Cortex.

Provides Settings loaded from YAML files and environment variables.
"""

from cortex.config.models import Settings
from cortex.config.loader import load_settings

__all__ = ["Settings", "load_settings"]
