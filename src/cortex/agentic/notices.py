"""PromotionNotices — the agentic side of the `rule.promoted` edge (#173).

A promotion waits here until the next chat turn reads it: the loop takes one
line per turn. The buffer is bounded because a line nobody read for weeks is
noise, and dropping it is the same loss a failed delivery already accepts.
"""

from __future__ import annotations

from collections import deque

from cortex.events.base import BaseEvent
from cortex.events.types import EventTypes


class PromotionNotices:
    """A bounded FIFO of promotion lines, fed by the bus, drained by the loop."""

    def __init__(self, capacity: int = 20) -> None:
        # A bounded deque drops the oldest line on overflow, which is exactly
        # the line the next turn would have read first anyway.
        self._lines: deque[str] = deque(maxlen=capacity)

    async def handle(self, event: BaseEvent) -> None:
        """Enqueue the line of a `rule.promoted` event; ignore anything else."""
        if event.type != EventTypes.RULE_PROMOTED:
            return
        line = event.payload.get("line")
        if isinstance(line, str) and line.strip():
            self._lines.append(line)

    def take(self) -> str | None:
        """Pop one line, oldest first; None when nothing is pending."""
        return self._lines.popleft() if self._lines else None
