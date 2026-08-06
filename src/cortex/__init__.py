"""
Cortex - Personal AI Assistant

An agentic system that learns user patterns, delegates tasks,
and evolves through interaction.
"""

__version__ = "0.1.0"

from cortex.config.models import Settings
from cortex.events import BaseEvent, EventBus, EventTypes
from cortex.logging import StructuredLogger, configure_logging

__all__ = [
    "__version__",
    "Settings",
    "configure_logging",
    "StructuredLogger",
    "EventBus",
    "BaseEvent",
    "EventTypes",
]
