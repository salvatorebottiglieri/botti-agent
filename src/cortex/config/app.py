"""HTTP server settings slice."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from cortex.config.base import CortexSettings


class AppSettings(CortexSettings):
    """FastAPI/uvicorn server settings (env namespace ``APP_``).

    ``reload`` and ``workers`` are carried over from the flat settings model
    unchanged; nothing in ``src/`` consumes them today.
    """

    model_config = SettingsConfigDict(env_prefix="APP_")

    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000, ge=1, le=65535)
    reload: bool = Field(default=False)
    workers: int = Field(default=1, ge=1)
