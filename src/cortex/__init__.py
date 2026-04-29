"""
Cortex - Personal AI Assistant

An agentic system that learns user patterns, delegates tasks,
and evolves through interaction.
"""

__version__ = "0.1.0"

from cortex.config.models import Settings
from cortex.logging import configure_logging, StructuredLogger
from cortex.events import EventBus, BaseEvent, EventTypes

__all__ = [
    "__version__",
    "Settings",
    "configure_logging",
    "StructuredLogger",
    "EventBus",
    "BaseEvent",
    "EventTypes",
]
