"""LearningModule: subscribes to minion events and drives the echo state network.

Pure event -> state -> readout wiring: no training, no storage. Readouts are
expected to be registered on the ESN before the getters are called; they are
resolved lazily by name so late registration stays supported.
"""

from typing import TypeVar

from cortex.events.base import BaseEvent
from cortex.events.bus import EventBus
from cortex.learning.encoding import MINION_EVENT_TYPES, MinionEventEncoder
from cortex.learning.readouts import AnomalyReadout, PatternReadout, SalienceReadout
from cortex.learning.reservoir import EchoStateNetwork, Readout

_ReadoutT = TypeVar("_ReadoutT", bound=Readout)

#: Names under which the three readouts are expected on the ESN.
SALIENCE_READOUT = "salience"
ANOMALY_READOUT = "anomaly"
PATTERN_READOUT = "pattern"


class LearningModule:
    """Advance an ESN one step per minion event and expose readout scores."""

    def __init__(
        self,
        event_bus: EventBus,
        esn: EchoStateNetwork,
        *,
        encoder: MinionEventEncoder | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._esn = esn
        self._encoder = encoder if encoder is not None else MinionEventEncoder()
        if self._encoder.n_features != self._esn.reservoir.n_input:
            raise ValueError(
                f"encoder.n_features ({self._encoder.n_features}) does not match "
                f"esn.reservoir.n_input ({self._esn.reservoir.n_input})"
            )
        self._subscribed = False

    async def subscribe(self) -> None:
        """Subscribe to every minion event type (one subscription each).

        Idempotent: repeated calls are no-ops, so a published event always
        advances the reservoir exactly once per bus dispatch.
        """
        if self._subscribed:
            return
        for event_type in MINION_EVENT_TYPES:
            await self._event_bus.subscribe(event_type, self.on_minion_event)
        self._subscribed = True

    async def on_minion_event(self, event: BaseEvent) -> None:
        """Advance the ESN by one step with the encoded event vector."""
        self._esn.step(self._encoder.encode(event))

    def _resolve_readout(self, name: str, readout_type: type[_ReadoutT]) -> _ReadoutT:
        """Return the readout registered under ``name``, typed as ``readout_type``."""
        try:
            readout = self._esn.get_readout(name)
        except KeyError:
            raise RuntimeError(
                f"Readout '{name}' is not registered on the ESN"
            ) from None
        if not isinstance(readout, readout_type):
            raise RuntimeError(
                f"Readout '{name}' is registered but is not a {readout_type.__name__}"
            )
        return readout

    def get_salience(self) -> float:
        """Salience score of the current reservoir state, clamped to [0, 1].

        Ridge regression on 0/1 targets can overshoot the unit range by a tiny
        margin, so the module guarantees the domain invariant that salience is
        a unit-range score. Raises RuntimeError if the ``salience`` readout is
        missing or untrained.
        """
        readout = self._resolve_readout(SALIENCE_READOUT, SalienceReadout)
        return min(1.0, max(0.0, readout.score(self._esn.reservoir.state)))

    def get_anomaly_score(self) -> float:
        """Reconstruction error of the current reservoir state.

        Raises RuntimeError if the ``anomaly`` readout is missing or untrained.
        """
        readout = self._resolve_readout(ANOMALY_READOUT, AnomalyReadout)
        return readout.reconstruction_error(self._esn.reservoir.state)

    def get_pattern_probabilities(self) -> dict[str, float]:
        """Pattern-class probabilities for the current reservoir state.

        Raises RuntimeError if the ``pattern`` readout is missing or untrained.
        """
        readout = self._resolve_readout(PATTERN_READOUT, PatternReadout)
        return readout.get_pattern_probabilities(self._esn.reservoir.state)
