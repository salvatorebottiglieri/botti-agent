"""Heartbeat and command message schemas."""

from __future__ import annotations

from pydantic import BaseModel


class HeartbeatMessage(BaseModel):
    """Heartbeat message sent by minion to Cortex."""

    minion_id: str
    timestamp: str  # ISO 8601
    status: str  # "healthy" | "degraded" | "error"
    battery_level: float | None = None
    network_type: str | None = None
    queue_size: int = 0
    last_sequence: int | None = None
    stats: dict[str, object] | None = None


class CommandMessage(BaseModel):
    """Command message sent from Cortex to minion."""

    command_id: str
    command: str  # "update_config" | "request_status" | etc.
    config: dict[str, object] | None = None


__all__ = [
    "HeartbeatMessage",
    "CommandMessage",
]
