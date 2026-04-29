"""LLM generation configuration."""

from typing import Any
from pydantic import BaseModel, Field


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
