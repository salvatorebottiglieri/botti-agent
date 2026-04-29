"""
Logging setup for Cortex.

Provides structured logging with trace ID propagation.
"""

from cortex.logging.setup import configure_logging
from cortex.logging.context import (
    get_trace_id,
    set_trace_id,
    clear_trace_id,
    StructuredLogger,
)

__all__ = [
    "configure_logging",
    "get_trace_id",
    "set_trace_id",
    "clear_trace_id",
    "StructuredLogger",
]
