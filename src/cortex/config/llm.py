"""LLM settings slice."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import AliasChoices, BaseModel, Field, SecretStr
from pydantic_settings import SettingsConfigDict

from cortex.config.base import CortexSettings

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


class LLMSettings(CortexSettings):
    """LLM provider, model and circuit-breaker settings (env namespace ``LLM_``).

    Lives under ``cortex.config`` rather than next to the LLM client: the
    composed root imports this slice, and importing it from ``cortex.llm`` would
    pull the ``cortex.llm`` package ``__init__`` (factory → provider → ``openai``
    SDK) into every process that only reads configuration.

    The three circuit-breaker fields keep the legacy unprefixed environment
    names they had as flat root fields (``CIRCUIT_BREAKER_THRESHOLD``,
    ``CIRCUIT_BREAKER_TIMEOUT``, ``CIRCUIT_BREAKER_HALF_OPEN_SUCCESSES``) in
    addition to the ``LLM_``-prefixed spellings: an operator's existing
    environment must not be silently ignored.
    """

    model_config = SettingsConfigDict(env_prefix="LLM_", populate_by_name=True)

    provider: Literal["openai", "anthropic"] = Field(
        default="openai",
        description="LLM provider to use",
    )
    api_key: SecretStr = Field(description="API key for the LLM provider")
    model: str = Field(default="deepseek-v4-flash", description="Model name to use")
    judge_model: str = Field(
        default="deepseek-v4-pro",
        description="Model used by the Trajectory Judge — distinct from llm_model "
        "so the judge never grades with the generator's model (self-enhancement "
        "bias guard, T5)",
    )
    base_url: str | None = Field(
        default=None,
        description="Base URL for API-compatible providers",
    )
    timeout: int = Field(default=60, ge=1)
    circuit_breaker_threshold: int = Field(
        default=5,
        ge=1,
        validation_alias=AliasChoices(
            "LLM_CIRCUIT_BREAKER_THRESHOLD", "CIRCUIT_BREAKER_THRESHOLD"
        ),
    )
    circuit_breaker_timeout: float = Field(
        default=30.0,
        ge=0.0,
        validation_alias=AliasChoices("LLM_CIRCUIT_BREAKER_TIMEOUT", "CIRCUIT_BREAKER_TIMEOUT"),
    )
    circuit_breaker_half_open_successes: int = Field(
        default=3,
        ge=1,
        validation_alias=AliasChoices(
            "LLM_CIRCUIT_BREAKER_HALF_OPEN_SUCCESSES",
            "CIRCUIT_BREAKER_HALF_OPEN_SUCCESSES",
        ),
    )
    pricing: dict[str, ModelPricing] = Field(
        default_factory=lambda: {
            # Defaults cover the shipped config model (deepseek-v4-flash) and the
            # Settings default (deepseek-v4-flash generator / deepseek-v4-pro
            # judge); v4 prices are DeepSeek's off-peak cache-miss input and
            # off-peak output rates (api-docs.deepseek.com/quick_start/pricing).
            # Override via LLM_PRICING (JSON) or constructor for any other model.
            "deepseek-v4-flash": ModelPricing(input_per_mtok=0.22, output_per_mtok=0.66),
            "deepseek-v4-pro": ModelPricing(input_per_mtok=0.66, output_per_mtok=1.98),
            "deepseek-chat": ModelPricing(input_per_mtok=0.27, output_per_mtok=1.10),
            "gpt-4o": ModelPricing(input_per_mtok=2.50, output_per_mtok=10.00),
        },
        description="Per-model token pricing in USD per 1M tokens",
    )
