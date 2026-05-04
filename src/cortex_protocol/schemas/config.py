"""Minion configuration schemas for YAML serialization."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SensorConfig(BaseModel):
    """Configuration for a single sensor."""

    enabled: bool = True
    sampling_interval: int = Field(default=60, ge=1)  # seconds
    significant_change: float | None = None  # meters for location
    debounce_seconds: int | None = None


class BatchConfig(BaseModel):
    """Configuration for event batching."""

    max_size: int = Field(default=50, ge=1, le=100)
    flush_interval: int = Field(default=30, ge=1, le=3600)  # seconds


class PrivacyConfig(BaseModel):
    """Configuration for privacy settings."""

    exclude_apps: list[str] = []
    exclude_locations: list[str] = []
    precision_reduction: str | None = None  # "city" | "neighborhood" | "precise"


class MinionConfig(BaseModel):
    """Full minion configuration, YAML-serializable."""

    version: int = 1
    sensors: dict[str, SensorConfig] = {}
    batch: BatchConfig = Field(default_factory=BatchConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)


__all__ = [
    "SensorConfig",
    "BatchConfig",
    "PrivacyConfig",
    "MinionConfig",
]
