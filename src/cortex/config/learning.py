"""Learning module settings slice."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from cortex.config.base import CortexSettings


class LearningSettings(CortexSettings):
    """ESN / reservoir hyperparameters (env namespace ``LEARNING_``).

    The single source of the reservoir configuration (ADR-0001):
    :meth:`cortex.learning.module.LearningModule.from_settings` builds the
    ``Reservoir`` from these values, so the defaults here — 500 neurons, 0.9
    spectral radius, 0.3 leak rate — are the defaults of the learning module.

    The prefix is load-bearing: without it the generic ``RESERVOIR_SIZE`` /
    ``SPECTRAL_RADIUS`` / ``LEAK_RATE`` become live, unprefixed configuration
    surface for the whole process.
    """

    model_config = SettingsConfigDict(env_prefix="LEARNING_")

    reservoir_size: int = Field(default=500, ge=1)
    spectral_radius: float = Field(default=0.9, gt=0, lt=1)
    input_scale: float = Field(default=1.0, gt=0)
    leak_rate: float = Field(default=0.3, gt=0, le=1)
    ridge_lambda: float = Field(default=1e-6, gt=0)
