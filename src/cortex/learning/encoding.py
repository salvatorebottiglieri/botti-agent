"""Encoding of minion bus events into fixed-size ESN input vectors.

Each vector is ``len(MINION_EVENT_TYPES) + len(NUMERIC_FIELDS)`` float64 values
in [-1, 1]: a one-hot block marking the event type followed by a numeric block
with normalized payload fields. Unknown event types encode as an all-zero
one-hot block, which is still a valid input vector.
"""

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
from numpy.typing import NDArray

from cortex.events.base import BaseEvent

#: Minion event types published on the bus by the minion event processor.
MINION_EVENT_TYPES: tuple[str, ...] = (
    "minion.location",
    "minion.activity",
    "minion.battery",
    "minion.app_usage",
    "minion.calendar",
    "minion.payment",
    "minion.screen_activity",
    "minion.application_focus",
    "minion.keyboard_activity",
    "minion.network_status",
)

#: Numeric payload fields with their normalization ranges, in fixed order.
#: This order defines the layout of the numeric block of every encoded vector:
#: latitude, longitude, accuracy, speed, heading, level, confidence,
#: duration_seconds.
NUMERIC_FIELDS: tuple[tuple[str, float, float], ...] = (
    ("latitude", -90.0, 90.0),
    ("longitude", -180.0, 180.0),
    ("accuracy", 0.0, 500.0),
    ("speed", 0.0, 100.0),
    ("heading", 0.0, 360.0),
    ("level", 0.0, 1.0),
    ("confidence", 0.0, 1.0),
    ("duration_seconds", 0.0, 86400.0),
)


def _normalize(value: float, lo: float, hi: float) -> float:
    """Linearly map ``value`` from [lo, hi] to [-1, 1], clamped.

    A degenerate zero-width range maps to 0.0.
    """
    if hi == lo:
        return 0.0
    scaled = 2.0 * (value - lo) / (hi - lo) - 1.0
    return max(-1.0, min(1.0, scaled))


class MinionEventEncoder:
    """Convert minion events into fixed-size reservoir input vectors."""

    def __init__(self, event_types: Sequence[str] = MINION_EVENT_TYPES) -> None:
        self._event_types = tuple(event_types)

    @property
    def n_features(self) -> int:
        """Total vector length: the one-hot block plus the numeric block."""
        return len(self._event_types) + len(NUMERIC_FIELDS)

    def encode(self, event: BaseEvent) -> NDArray[np.float64]:
        """Encode an event as a float64 vector of ``n_features`` values in [-1, 1].

        The one-hot bit is set for the matching event type (all-zero for unknown
        types). Numeric fields are read from the nested ``payload["payload"]``
        dict when present, falling back to ``event.payload`` itself; missing
        fields normalize to 0.0.
        """
        vector = np.zeros(self.n_features, dtype=np.float64)
        try:
            hot_index = self._event_types.index(event.type)
        except ValueError:
            pass
        else:
            vector[hot_index] = 1.0

        nested = event.payload.get("payload")
        data: dict[str, Any] = nested if isinstance(nested, dict) else event.payload

        offset = len(self._event_types)
        for i, (field, lo, hi) in enumerate(NUMERIC_FIELDS):
            value = data.get(field)
            if value is None:
                continue  # missing field stays 0.0
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue  # non-numeric field stays 0.0
            if not math.isfinite(value):
                continue  # non-finite (NaN, +inf, -inf) field stays 0.0
            vector[offset + i] = _normalize(value, lo, hi)

        return vector
