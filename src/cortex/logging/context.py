"""
Context management for logging (trace IDs).
"""

import contextvars
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import structlog

# Context variable for trace ID
_trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")


def get_trace_id() -> str:
    """Get the current trace ID, or empty string if not set."""
    return _trace_id_var.get()


def set_trace_id(trace_id: str | None = None) -> str:
    """
    Set the trace ID for the current context.

    Args:
        trace_id: The trace ID to set. If None, generates a new UUID.

    Returns:
        The trace ID that was set.
    """
    if trace_id is None:
        trace_id = str(uuid.uuid4())[:8]
    _trace_id_var.set(trace_id)
    return trace_id


def clear_trace_id() -> None:
    """Clear the trace ID from the current context."""
    _trace_id_var.set("")


@contextmanager
def trace_context(trace_id: str | None = None) -> Iterator[str]:
    """
    Context manager for trace ID.

    Sets a trace ID for the duration of the context, then restores the previous value.

    Args:
        trace_id: The trace ID to use. If None, generates a new UUID.

    Example:
        with trace_context():
            logger.info("this will have a trace_id")
            # ... do work ...
    """
    previous = get_trace_id()
    new_id = set_trace_id(trace_id)
    try:
        yield new_id
    finally:
        _trace_id_var.set(previous)


class StructuredLogger:
    """
    Wrapper for structlog that provides convenience methods.

    Example:
        logger = StructuredLogger(__name__)
        logger.info("message", key="value")
        logger.info_with_trace("message")  # Inherits trace_id from context
    """

    def __init__(self, name: str):
        self._logger = structlog.get_logger(name)

    def _log(self, level: str, msg: str, **kwargs: Any) -> None:
        """Log at the specified level."""
        # structlog stdlib proxy expects 'event' for the message
        kwargs['event'] = msg
        getattr(self._logger, level)(**kwargs)

    def debug(self, msg: str, **kwargs: Any) -> None:
        self._log("debug", msg, **kwargs)

    def info(self, msg: str, **kwargs: Any) -> None:
        self._log("info", msg, **kwargs)

    def warning(self, msg: str, **kwargs: Any) -> None:
        self._log("warning", msg, **kwargs)

    def error(self, msg: str, **kwargs: Any) -> None:
        self._log("error", msg, **kwargs)

    def critical(self, msg: str, **kwargs: Any) -> None:
        self._log("critical", msg, **kwargs)

    def exception(self, msg: str, **kwargs: Any) -> None:
        self._log("exception", msg, **kwargs)

    def with_context(self, **kwargs: Any) -> "StructuredLogger":
        """Return a new logger with additional context."""
        new_logger = StructuredLogger(self._logger.name)
        new_logger._logger = self._logger.bind(**kwargs)
        return new_logger
