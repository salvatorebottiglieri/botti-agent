"""JSON Schema exports for non-Python minion implementations."""

from __future__ import annotations

import json
from pathlib import Path

# Lazy-loaded schemas (generated from Pydantic models at import time)
_schemas_cache: dict[str, object] | None = None


def _get_schemas() -> dict[str, object]:
    """Generate schemas from Pydantic models on first access."""
    global _schemas_cache
    if _schemas_cache is not None:
        return _schemas_cache

    from cortex_protocol.schemas import (
        ActivityEvent,
        ApplicationFocusEvent,
        AppUsageEvent,
        BatteryEvent,
        CalendarEvent,
        CallLogEvent,
        CommandMessage,
        HeartbeatMessage,
        KeyboardActivityEvent,
        LocationEvent,
        MinionConfig,
        MinionEventBatch,
        NetworkStatusEvent,
        PaymentEvent,
        RefundEvent,
        ScreenActivityEvent,
    )

    # All events + config + messages
    model_to_name = {
        LocationEvent: "location_event",
        ActivityEvent: "activity_event",
        CalendarEvent: "calendar_event",
        AppUsageEvent: "app_usage_event",
        CallLogEvent: "call_log_event",
        PaymentEvent: "payment_event",
        RefundEvent: "refund_event",
        ScreenActivityEvent: "screen_activity_event",
        ApplicationFocusEvent: "application_focus_event",
        KeyboardActivityEvent: "keyboard_activity_event",
        BatteryEvent: "battery_event",
        NetworkStatusEvent: "network_status_event",
        MinionEventBatch: "minion_event_batch",
        MinionConfig: "minion_config",
        CommandMessage: "command_message",
        HeartbeatMessage: "heartbeat_message",
    }

    _schemas_cache = {}
    for model_cls, name in model_to_name.items():
        _schemas_cache[name] = model_cls.model_json_schema()

    return _schemas_cache


def get_schema(name: str) -> object:
    """Get a specific schema by name.

    Names: location_event, activity_event, calendar_event, app_usage_event,
    call_log_event, payment_event, refund_event, screen_activity_event,
    application_focus_event, keyboard_activity_event, battery_event,
    network_status_event, minion_event_batch, minion_config,
    command_message, heartbeat_message
    """
    schemas = _get_schemas()
    if name not in schemas:
        raise KeyError(f"Unknown schema: {name}. Available: {list(schemas.keys())}")
    return schemas[name]


def get_all_schemas() -> dict[str, object]:
    """Get all schemas as a dictionary."""
    return _get_schemas().copy()


def export_to_directory(directory: str | Path) -> None:
    """Export all schemas as JSON files to a directory.

    Args:
        directory: Path to write JSON files to.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    schemas = _get_schemas()
    for name, schema in schemas.items():
        filepath = directory / f"{name}.json"
        with open(filepath, "w") as f:
            json.dump(schema, f, indent=2)


__all__ = [
    "get_schema",
    "get_all_schemas",
    "export_to_directory",
]
