"""Learning module settings slice."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from cortex.config.base import CortexSettings


class LearningSettings(CortexSettings):
    """ESN / reservoir hyperparameters (env namespace ``LEARNING_``).

    Stub by contract (#35): the reservoir's current defaults live on
    :class:`cortex.learning.reservoir.EchoStateNetwork`, wired directly at
    construction. Wiring these values — and reconciling the code's
    ``spectral_radius=1.0`` with ADR-0001's 0.9 — is #12, together with the
    range validation that belongs with them.

    The prefix is load-bearing even while nothing reads the fields: without it
    the generic ``RESERVOIR_SIZE`` / ``SPECTRAL_RADIUS`` / ``LEAK_RATE`` become
    live, unprefixed configuration surface for the whole process.
    """

    model_config = SettingsConfigDict(env_prefix="LEARNING_")

    reservoir_size: int = Field(default=500)
    spectral_radius: float = Field(default=0.9)
    leak_rate: float = Field(default=0.3)
