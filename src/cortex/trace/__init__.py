"""Cortex Trace Module.

Persistence layer for loop-trace capture (issue #111 T1): the opt-in
``trace_enabled`` session flag plus the ``loop_events`` store behind
TraceRepository. Capture wiring (turning AgentLoop events into inserts)
lands in a later ticket.
"""

from cortex.trace.interfaces import TraceRepository
from cortex.trace.models import TraceEvent
from cortex.trace.repository import PostgresTraceRepository

__all__ = [
    # Models
    "TraceEvent",
    # Interfaces
    "TraceRepository",
    # Implementations
    "PostgresTraceRepository",
]
