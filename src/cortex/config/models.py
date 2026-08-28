"""
Pydantic settings models for Cortex configuration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from cortex.llm.models import UsageStats


class ModelPricing(BaseModel):
    """Per-model token pricing, in USD per 1M tokens (mtok).

    Mirrors the published per-model price sheets (OpenAI, DeepSeek):
    input tokens and output tokens are priced independently.
    """

    input_per_mtok: float
    output_per_mtok: float


def derive_cost(usage: UsageStats, pricing: ModelPricing) -> float:
    """Derive the USD cost of a call's token usage from per-model pricing.

    Pure and deterministic: same usage + pricing always yields the same cost.
    ``total_tokens`` is informational — cost is driven by the
    prompt/completion split because the two directions are priced differently.
    """
    return (
        usage.prompt_tokens * pricing.input_per_mtok
        + usage.completion_tokens * pricing.output_per_mtok
    ) / 1_000_000


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
    circuit_breaker_threshold: int = Field(default=5, ge=1)
    circuit_breaker_timeout: float = Field(default=30.0, ge=0.0)
    circuit_breaker_half_open_successes: int = Field(default=3, ge=1)
    llm_timeout: int = Field(default=60, ge=1)
    llm_pricing: dict[str, ModelPricing] = Field(
        default_factory=lambda: {
            # Defaults cover the shipped config model and the Settings default;
            # override via LLM_PRICING (JSON) or constructor for any other model.
            "deepseek-chat": ModelPricing(input_per_mtok=0.27, output_per_mtok=1.10),
            "gpt-4o": ModelPricing(input_per_mtok=2.50, output_per_mtok=10.00),
        },
        description="Per-model token pricing in USD per 1M tokens",
    )

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
