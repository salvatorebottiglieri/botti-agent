"""LLM generation parameters.

The LLM *settings* slice moved to :mod:`cortex.config.llm` (F-A, issue #35) so
that the config layer does not import the ``cortex.llm`` package: this module's
package ``__init__`` eagerly imports the factory and the OpenAI provider, which
would load the ``openai`` SDK for every process that only reads configuration.
The names below are re-exported unchanged for existing importers.
"""

from typing import Any

from pydantic import BaseModel, Field

from cortex.config.llm import LLMSettings, ModelPricing, derive_cost

__all__ = ["GenerationConfig", "LLMSettings", "ModelPricing", "derive_cost"]


class GenerationConfig(BaseModel):
    """
    Configuration for LLM generation.

    All parameters are optional with sensible defaults.
    """

    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    top_k: int | None = Field(default=None, ge=1)
    frequency_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    presence_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    stop: str | list[str] | None = None
    seed: int | None = None
    response_format: dict[str, Any] | None = None  # For JSON mode

    model_config = {
        "extra": "allow",
    }
