"""
Logging setup for Cortex.

Provides structured logging with trace ID propagation.
"""

from cortex.logging.context import (
    StructuredLogger,
    clear_trace_id,
    get_trace_id,
    set_trace_id,
)
from cortex.logging.setup import configure_logging

__all__ = [
    "configure_logging",
    "get_trace_id",
    "set_trace_id",
    "clear_trace_id",
    "StructuredLogger",
]
