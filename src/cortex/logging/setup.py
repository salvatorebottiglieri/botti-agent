"""
Logging configuration for Cortex.
"""

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog
from structlog.types import Processor

from cortex.config.models import Settings


def configure_logging(settings: Settings | None = None) -> None:
    """
    Configure structured logging for Cortex.

    Args:
        settings: Application settings. If None, loads from config.
    """
    if settings is None:
        from cortex.config.loader import get_settings
        settings = get_settings()

    # Determine processors based on format
    shared_processors: list[Processor] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if settings.log_include_trace_id:
        shared_processors.insert(0, structlog.contextvars.merge_contextvars)
        shared_processors.insert(2, _inject_trace_id)

    if settings.log_format == "json":
        # JSON output for production
        processors = shared_processors + [
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ]
    else:
        # Console output for development
        try:
            import colorama
            colorama.just_fix_windows_console()
            processors = shared_processors + [
                structlog.dev.ConsoleRenderer(colors=True),
            ]
        except ImportError:
            processors = shared_processors + [
                structlog.dev.ConsoleRenderer(colors=False),
            ]

    # Configure structlog
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level),
    )

    # Reduce noise from third-party libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _inject_trace_id(
    logger: logging.Logger,
    method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Inject trace_id from context into log event."""
    from cortex.logging.context import _trace_id_var

    trace_id = _trace_id_var.get()
    if trace_id:
        event_dict["trace_id"] = trace_id
    return event_dict
