# cortex-protocol

Language-agnostic protocol package for Cortex minions.

## Installation

```bash
pip install cortex-protocol
```

## Usage

```python
from cortex_protocol import LocationEvent, MinionEventBatch, MQTTTopics

# Create an event
event = LocationEvent(
    occurred_at=datetime.utcnow(),
    payload={
        "latitude": 41.9028,
        "longitude": 12.4964,
        "accuracy": 10.0,
    }
)

# Serialize batch to JSON
batch = MinionEventBatch(...)
json_str = batch.model_dump_json()

# Get MQTT topic for a minion
topic = MQTTTopics.events("minion-123")
```

## Export JSON Schemas

For non-Python minion implementations:

```python
from cortex_protocol.schemas.jsonschema import export_to_directory

export_to_directory("./schemas")
```

This generates JSON Schema files for all event types.

## Event Types

- `LocationEvent` — GPS coordinates from phone
- `ActivityEvent` — Physical activity detected
- `CalendarEvent` — Calendar entry
- `AppUsageEvent` — App usage summary
- `CallLogEvent` — Phone call
- `PaymentEvent` — Card transaction
- `RefundEvent` — Card refund
- `ScreenActivityEvent` — Screen on/off, active window
- `ApplicationFocusEvent` — App switch
- `KeyboardActivityEvent` — Keystroke/mouse summary (aggregated)
- `BatteryEvent` — Battery level change
- `NetworkStatusEvent` — Network connectivity change
