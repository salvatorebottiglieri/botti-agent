# Minion protocol

> **Current truth** for how sensor agents ("minions") register, authenticate,
> send events and receive config. Consolidates the former `MINION_PROTOCOL.md`
> and `MINION_EVENTS.md`, verified against the shipped code. Decisions and
> rejected alternatives (TLS, mTLS, SDK) are in the design history; the
> authoritative schemas are `src/cortex_protocol/`.

## Sources of truth

- `src/cortex_protocol/schemas/` — the Pydantic schemas, topics and enums. If
  code and this document disagree, the code wins and this document is wrong.
- `CONTEXT.md` — vocabulary.
- GitHub issues — planned work.
- This document — protocol and event catalogue. **Keep it in sync.**

## Transport

MQTT (`aiomqtt` on both sides), broker Mosquitto in `docker-compose.yml`. No TLS
in v1 (trusted network); E2E encryption is a v2 item. QoS 0 and 1 are used;
QoS 2 is defined but unused. Broker persistence holds messages for offline
minions.

## Authentication

Two layers:

- **Minion → broker**: MQTT username/password. `POST /admin/minions/{id}/token`
  generates a `minion_<random>` password, stores its hash in `api_keys` (name
  `minion:{id}:{name}`) and writes the plaintext to the Mosquitto `passwd.conf`.
  `DELETE /admin/minions/{id}/token` revokes it and removes it from `passwd.conf`.
  Tokens are shown once, rotated manually from the admin UI.
- **Admin → Cortex HTTP**: hashed API keys in `api_keys` (`cortex token:create`).

## Topics

`MQTTTopics` (`cortex_protocol/schemas/topics.py`):

| Direction | Topic | Purpose |
|---|---|---|
| Minion → Cortex | `cortex/minions/{minion_id}/register` | Registration |
| Minion → Cortex | `cortex/minions/{minion_id}/events` | Event batches |
| Minion → Cortex | `cortex/minions/{minion_id}/heartbeat` | Heartbeat |
| Cortex → Minion | `cortex/minions/{minion_id}/commands/register` | Registration confirmation |
| Cortex → Minion | `cortex/minions/{minion_id}/commands/config` | Config push |
| Cortex → Minion | `cortex/minions/{minion_id}/commands/status` | Status request |
| Cortex → Minion | `cortex/minions/{minion_id}/commands/#` | Command wildcard |

## Registration and heartbeat

Registration publishes `MinionInfo` (id, name, device type, capabilities,
metadata). The registry (`minions/registry.py`) has in-memory and Postgres
implementations; the `minions` table holds state and `last_heartbeat_at`.

`HeartbeatMessage` carries `minion_id`, ISO-8601 `timestamp`, `status`
(`healthy`/`degraded`/`error`), optional `battery_level` and `network_type`,
`queue_size`, `last_sequence`, and free-form `stats`. Heartbeats drive the
minion's state (`connecting`/`online`/`away`/`offline`).

`CommandMessage` carries `command_id`, `command` (`update_config`,
`request_status`, …) and optional `config`.

## Event batch envelope

`MinionEventBatch` (`schemas/envelopes.py`):

```python
MinionEventBatch:
  metadata: MinionEventMetadata
    minion_id: UUID
    minion_type: str          # "phone" | "card" | "laptop"
    sequence: int             # monotonic counter
    batch_id: UUID
    device_time: datetime
    cortex_received_at: datetime | None
  events: list[MinionEvent]
```

Each event carries `type` (the literal discriminator, e.g. `"location"`),
`occurred_at`, and its typed `payload`. Batches allow gap detection via
`sequence` and idempotent ingest via `batch_id`.

## Event catalogue

Twelve event types, all in `cortex_protocol/schemas/events.py`:

| Event `type` | Source | Key payload fields |
|---|---|---|
| `location` | Phone | lat/long, accuracy, altitude, speed, heading, source (`gps`/`network`/`fused`), speed_category |
| `activity` | Phone | `activity_type`, confidence, start_time, duration_seconds |
| `calendar` | Phone | calendar_id, event_id, title, start/end, all_day, timezone, location, attendees |
| `app_usage` | Phone | app, duration, `usage_type` (`foreground`/`background`/`system`) |
| `call_log` | Phone | direction, duration, anonymized counterparty |
| `payment` | Card | amount, merchant, `MerchantCategory`, location, `TransactionType` |
| `refund` | Card | amount, original transaction id, status |
| `screen_activity` | Laptop | screen on/off, window title, idle transitions |
| `application_focus` | Laptop | app, `AppCategory`, duration, window title |
| `keyboard_activity` | Laptop | aggregated keystroke/mouse/scroll counts |
| `battery` | Phone/Laptop | level, charging, health |
| `network_status` | Phone/Laptop | `NetworkType` (wifi/cellular/ethernet/bluetooth/none), ssid, signal, vpn |

Enums in `schemas/enums.py`: `ActivityType`, `UsageType`, `MerchantCategory`,
`TransactionType`, `AppCategory`, `NetworkType`.

Privacy defaults (from the event catalogue): `location` on with optional
precision reduction; `calendar` per-calendar permissions; `app_usage`
whitelist-only; `call_log` anonymized; `payment` reducible to category-only;
`screen_activity` app name only (window titles off by default);
`keyboard_activity` aggregated counts only.

## Configuration push

`MinionConfig` (`schemas/config.py`):

```python
MinionConfig:
  version: int = 1
  sensors: dict[str, SensorConfig]   # enabled, sampling_interval, significant_change, debounce_seconds
  batch: BatchConfig                 # max_size (<=100), flush_interval (<=3600s)
  privacy: PrivacyConfig             # exclude_apps, exclude_locations, precision_reduction
```

Config is pushed on the `commands/config` topic and acknowledged by the minion.

## Server side

`MinionMQTTClient` (`minions/mqtt_client.py`) subscribes as configured, parses
payloads, extracts the minion id from the topic, and dispatches to
`MinionEventHandler`s. `MinionService` (`services/minion_service.py`) turns
events into facts/events on the Cortex bus.

## Client side (laptop-minion)

`laptop-minion/` is a standalone package with its own `pyproject.toml` and tests.
Sensors: `screen` (active window, idle), `keyboard` (aggregated),
`network`, `battery`. CLI: `run`, `status`, `init`. It queues events locally and
flushes batches to the events topic.

## Flow

```mermaid
sequenceDiagram
    autonumber
    participant M as Minion
    participant B as Mosquitto
    participant GW as MinionMQTTClient
    participant S as MinionService
    participant F as FactExtractor / FactStore
    participant BUS as Event bus
    participant DB as Postgres

    M->>B: CONNECT (username/password)
    M->>B: publish register
    B->>GW: register
    GW->>S: MinionInfo
    S->>DB: upsert minion
    loop every batch / flush interval
        M->>B: publish MinionEventBatch
        B->>GW: events
        GW->>S: handle_batch
        S->>S: check sequence gap
        S->>F: extract_from_event_type
        F->>DB: upsert facts
        S->>BUS: minion.event.&lt;type&gt;
    end
    M->>B: publish heartbeat
    B->>GW: heartbeat
    GW->>S: heartbeat
    S->>DB: update state + last_heartbeat_at
```

## Status

The protocol schemas (`src/cortex_protocol/`) and the laptop client are
implemented and agree with each other. **The server-side gateway is not wired to
them yet** — this is a known gap, not a documented design:

| Layer | Speaks | Evidence |
|---|---|---|
| Protocol / laptop minion | topic `cortex/minions/{id}/events`, batch `{metadata, events}`, events with `type` | `cortex_protocol/schemas/topics.py:10`, `envelopes.py:24`, `events.py:46`; `laptop-minion/.../mqtt_client.py:318` |
| Server gateway | subscribes `minions/+/events`; expects `event_type` key and its own `MinionEventBatch` dataclass | `src/cortex/main.py:364`, `src/cortex/minions/mqtt_client.py:176,184`, `models.py:215` |

Consequences: `_extract_minion_id()` rejects `cortex/minions/...` (parts[0] is
`cortex`), and a protocol event's `type` key is read as `event_type`
(`custom.event`), so no facts are extracted. The unit tests cover each side
against its own shape, so neither catches the mismatch. Phone and card minions
are specified but not built.
