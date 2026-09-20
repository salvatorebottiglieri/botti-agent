"""Pydantic settings models for Cortex configuration.

The root :class:`Settings` is a composition of per-module slices (ADR-0010):
each slice is handed to its consumer by constructor injection, so a module can
be tested with its own config without mutating unrelated ones.
"""

from __future__ import annotations

from pydantic import Field

from cortex.config.app import AppSettings
from cortex.config.base import CortexSettings
from cortex.config.database import DatabaseSettings
from cortex.config.learning import LearningSettings
from cortex.config.logging import LoggingSettings
from cortex.config.mqtt import MQTTSettings
from cortex.config.trace import TraceSettings
from cortex.llm.config import LLMSettings, ModelPricing, derive_cost

__all__ = [
    "AppSettings",
    "DatabaseSettings",
    "LLMSettings",
    "LearningSettings",
    "LoggingSettings",
    "MQTTSettings",
    "ModelPricing",
    "Settings",
    "TraceSettings",
    "derive_cost",
]


class Settings(CortexSettings):
    """
    Root settings container for Cortex.

    Holds one instance of each per-module slice. Composed fields read their own
    environment namespace (``DB_``/``DATABASE_``, ``LLM_``, ``MQTT_``, ``APP_``,
    ``LOG_``, ``TRACE_``) and their own ``.env``; pass a slice explicitly to
    override it, e.g. ``Settings(llm={"api_key": ...})``.
    """

    version: str = Field(default="0.1.0")

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    # ``LLMSettings`` has a required ``api_key`` that the environment supplies,
    # so mypy rejects the bare class as a zero-arg factory. ``model_validate({})``
    # runs the same settings sources (env / .env) and keeps the required-field
    # check, so a missing LLM_API_KEY still raises ValidationError.
    llm: LLMSettings = Field(default_factory=lambda: LLMSettings.model_validate({}))
    mqtt: MQTTSettings = Field(default_factory=MQTTSettings)
    app: AppSettings = Field(default_factory=AppSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    trace: TraceSettings = Field(default_factory=TraceSettings)
    learning: LearningSettings = Field(default_factory=LearningSettings)

    def __repr__(self) -> str:
        """Hide sensitive values in repr."""
        return f"Settings(version={self.version!r})"
