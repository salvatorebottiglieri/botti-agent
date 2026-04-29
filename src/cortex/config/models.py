"""
Pydantic settings models for Cortex configuration.
"""

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Root settings container for Cortex.
    
    All modules import Settings from here to ensure consistent configuration.
    Uses env vars with prefixes: DB_, LLM_, MQTT_, APP_, LOG_
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    # ─── Database ────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/cortex",
        description="PostgreSQL connection URL"
    )
    db_pool_min_size: int = Field(default=5, ge=1)
    db_pool_max_size: int = Field(default=20, ge=1)
    db_pool_timeout: int = Field(default=30, ge=1)

    # ─── LLM ──────────────────────────────────────────────────
    llm_provider: Literal["openai", "anthropic"] = Field(
        default="openai",
        description="LLM provider to use"
    )
    llm_api_key: SecretStr = Field(
        description="API key for the LLM provider"
    )
    llm_model: str = Field(
        default="gpt-4o",
        description="Model name to use"
    )
    llm_base_url: str | None = Field(
        default=None,
        description="Base URL for API-compatible providers"
    )
    llm_timeout: int = Field(default=60, ge=1)

    # ─── MQTT ─────────────────────────────────────────────────
    mqtt_broker_url: str = Field(
        default="mqtt://localhost:1883",
        description="MQTT broker URL"
    )
    mqtt_username: str | None = Field(default=None)
    mqtt_password: SecretStr | None = Field(default=None)
    mqtt_client_id_prefix: str = Field(default="cortex")
    mqtt_keepalive: int = Field(default=60, ge=1)
    mqtt_reconnect_interval: int = Field(default=5, ge=1)

    # ─── App Server ───────────────────────────────────────────
    app_host: str = Field(default="0.0.0.0")
    app_port: int = Field(default=8000, ge=1, le=65535)
    app_reload: bool = Field(default=False)
    app_workers: int = Field(default=1, ge=1)

    # ─── Logging ──────────────────────────────────────────────
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO"
    )
    log_format: Literal["json", "console"] = Field(default="console")
    log_include_trace_id: bool = Field(default=True)

    # ─── Version ──────────────────────────────────────────────
    version: str = Field(default="0.1.0")

    def __repr__(self) -> str:
        """Hide sensitive values in repr."""
        return f"Settings(version={self.version!r})"
