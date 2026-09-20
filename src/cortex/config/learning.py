"""Learning module settings slice."""

from __future__ import annotations

from pydantic import Field

from cortex.config.base import CortexSettings


class LearningSettings(CortexSettings):
    """ESN / reservoir hyperparameters (#12 declares them; nothing reads them yet).

    Stub by contract (#35): the reservoir's current defaults live on
    :class:`cortex.learning.reservoir.EchoStateNetwork`, wired directly at
    construction. Wiring these values — and reconciling the code's
    ``spectral_radius=1.0`` with ADR-0001's 0.9 — is #12, together with the
    range validation that belongs with them.
    """

    reservoir_size: int = Field(default=500)
    spectral_radius: float = Field(default=0.9)
    leak_rate: float = Field(default=0.3)
