"""Cortex Trace Module.

Persistence layer for loop-trace capture: the opt-in ``trace_enabled`` session
flag plus the ``loop_events`` store behind TraceRepository (issue #111 T1),
and the T2 capture machinery — TraceRecorder (a consumer of the LoopEvent
stream that pseudonymizes PII-bearing fields before storage) behind the
Pseudonymizer interface with the local rizzo-pii HTTP implementation.
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
