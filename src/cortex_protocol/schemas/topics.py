"""MQTT topic constants and QoS definitions."""

from __future__ import annotations


class MQTTTopics:
    """MQTT topic constants for minion communication."""

    @staticmethod
    def events(minion_id: str) -> str:
        """Topic for minion event batches."""
        return f"cortex/minions/{minion_id}/events"

    @staticmethod
    def heartbeat(minion_id: str) -> str:
        """Topic for minion heartbeats."""
        return f"cortex/minions/{minion_id}/heartbeat"

    @staticmethod
    def register(minion_id: str) -> str:
        """Topic for minion registration."""
        return f"cortex/minions/{minion_id}/register"

    @staticmethod
    def commands(minion_id: str) -> str:
        """Topic for receiving commands from Cortex (wildcard)."""
        return f"cortex/minions/{minion_id}/commands/#"

    @staticmethod
    def command_register(minion_id: str) -> str:
        """Topic for registration confirmation from Cortex."""
        return f"cortex/minions/{minion_id}/commands/register"

    @staticmethod
    def command_config(minion_id: str) -> str:
        """Topic for config push from Cortex."""
        return f"cortex/minions/{minion_id}/commands/config"

    @staticmethod
    def command_status(minion_id: str) -> str:
        """Topic for status request from Cortex."""
        return f"cortex/minions/{minion_id}/commands/status"


class QoS:
    """MQTT Quality of Service levels."""

    AT_MOST_ONCE = 0  # Fire and forget
    AT_LEAST_ONCE = 1  # Acknowledged delivery
    EXACTLY_ONCE = 2  # Handshake (not used for v1)


__all__ = [
    "MQTTTopics",
    "QoS",
]
