"""Logging settings slice."""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from cortex.config.base import CortexSettings


class LoggingSettings(CortexSettings):
    """structlog configuration (env namespace ``LOG_``)."""

    model_config = SettingsConfigDict(env_prefix="LOG_")

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(default="INFO")
    format: Literal["json", "console"] = Field(default="console")
    include_trace_id: bool = Field(default=True)
