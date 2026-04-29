# Minion Protocol Design

> How minions communicate with Cortex. Design for v1.
> Created: 2026-04-28
> Updated: 2026-04-29 (removed mTLS, simplified to API tokens)

---

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         MINION                                   │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐ │
│  │ Data         │→ │ Filter &     │→ │ MQTT Client            │ │
│  │ Collectors   │  │ Normalizer   │  │ (with API token)       │ │
│  │ (GPS, API)   │  │ (debounce,    │  │                        │ │
│  │              │  │  dedup)       │  │                        │ │
│  └──────────────┘  └──────────────┘  └───────────┬────────────┘ │
└─────────────────────────────────────────────────┼───────────────┘
                                                  │ MQTT
                                                  ▼
                                        ┌─────────────────┐
                                        │   MQTT Broker   │
                                        │   (no TLS)      │
                                        └────────┬────────┘
                                                 │
                                        ┌────────┴────────┐
                                        │        ▼        │
                                        │   CORTEX        │
                                        │   (brain)       │
                                        │                 │
                                        │ MQTT Client     │
                                        │ (subscribes to  │
                                        │  minion topics) │
                                        └─────────────────┘
```

**Note:** For v1, we run on a trusted local network (e.g., home network). TLS/mTLS can be added later when minions need to connect over untrusted networks.

---

## Transport: MQTT

| Aspect | Decision |
|--------|----------|
| Broker | Eclipse Mosquitto |
| Protocol | MQTT 5.0 |
| Transport | Plain MQTT (no TLS for v1) |
| QoS | QoS 1 (at-least-once delivery) |
| Persistence | Broker-based (broker stores messages for offline minions) |

### Why MQTT (without TLS)?

| Benefit | Impact |
|---------|--------|
| Persistent connection | Lower latency, no connection overhead |
| QoS guarantees | Reliable event delivery |
| Offline buffering | Broker stores events while minion is offline |
| Keep-alive built-in | Automatic dead connection detection |
| Battery efficient | Long-lived TCP connection, minimal overhead |
| Bidirectional | Easy to add Cortex→Minion commands later |

**Security model:** v1 runs on a trusted network. Authentication is via API tokens embedded in MQTT usernames.

---

## Authentication: API Token

For v1, we use simple API token authentication:

| Aspect | Decision |
|--------|----------|
| Method | MQTT username/password (token-based) |
| Token format | UUID v4 |
| Storage | Token stored in minion config + Cortex DB |
| Rotation | Manual (user regenerates from Cortex UI) |

### Token Management

| Task | How |
|------|-----|
| Generate token | Cortex UI generates random UUID |
| Provision minion | User copies token to minion config |
| Authenticate | Minion connects with username=`minion_<id>`, password=<token> |
| Revoke | Delete token from Cortex DB |
| Verify | Broker checks username+password against auth plugin |

### Why Not mTLS (v1)?

| mTLS Complexity | Our v1 Approach |
|----------------|-----------------|
| Certificate generation per device | Simple UUID token |
| PKI infrastructure (CA) | No CA needed |
| Certificate rotation automation | Manual rotation (rarely needed) |
| Key storage on devices | Token in config file |
| CRL/OCSP management | Delete from DB |

mTLS is appropriate when:
- Minions connect over untrusted networks (internet)
- Regulatory compliance requires certificate-based auth
- You have infrastructure to manage certificate lifecycle

For a personal assistant running on your home network, API tokens are sufficient.

---

## Registration Flow

> Minion connects with a pre-provisioned API token.

### Pre-requisites

```
Minion configured with:
├── Broker URL (e.g., mqtt://192.168.1.100:1883)
├── Minion ID (UUID)
└── API Token (UUID, from Cortex UI)
```

### Initial Connection

```
┌──────────┐                           ┌──────────┐
│  Minion  │                           │  Cortex  │
└────┬─────┘                           └────┬─────┘
     │                                      │
     │  1. MQTT connect                      │
     │     username: minion_<id>            │
     │     password: <token>                │
     │ ──────────────────────────────────► │
     │                                      │
     │                                      │  2. Broker verifies token
     │                                      │     (via auth plugin or webhook)
     │                                      │
     │  3. CONNACK (success/failure)        │
     │ ◄──────────────────────────────────── │
     │                                      │
     │  4. Subscribe to commands topic      │
     │     cortex/minions/<minion_id>/commands/#
     │                                      │
     │  5. Publish register event          │
     │ ──────────────────────────────────► │
     │                                      │
     │                                      │  6. Cortex creates/updates
     │                                      │     minion record in DB
     │                                      │
     │  7. Registration confirmed          │
     │     (via command topic)              │
     │ ◄──────────────────────────────────── │
```

### Registration Event (over MQTT)

```
Topic: cortex/minions/<minion_id>/register
QoS: 1
Payload:
{
  "minion_id": "uuid",
  "minion_type": "phone",
  "minion_version": "1.0.0",
  "capabilities": ["location", "calendar"],
  "device_info": {
    "os": "Android 14",
    "app_version": "1.0.0"
  }
}
```

### Registration Confirmation (from Cortex)

```
Topic: cortex/minions/<minion_id>/commands/register
Payload:
{
  "status": "registered",
  "heartbeat_interval": 300,
  "batch_size": 50,
  "supported_event_types": ["location", "payment", "activity"]
}
```

---

## Topic Structure

```
cortex/
├── minions/
│   └── <minion_id>/
│       ├── events              # Minion → Cortex (events)
│       ├── heartbeat           # Minion → Cortex (heartbeat)
│       ├── register            # Minion → Cortex (registration)
│       └── commands/           # Cortex → Minion
│           ├── register       # Registration confirmation
│           └── config         # Config updates
```

---

## Event Flow

### Publishing Events (Minion → Cortex)

```
┌──────────┐                                        ┌──────────┐
│  Minion  │                                        │  Cortex  │
└────┬─────┘                                        └────┬─────┘
     │                                                  │
     │  1. Collect sensor data                          │
     │                                                  │
     │  2. Batch events (up to batch_size or timeout)  │
     │                                                  │
     │  3. Publish to MQTT                              │
     │  Topic: cortex/minions/<minion_id>/events        │
     │  QoS: 1                                          │
     │  Payload: {                                     │
     │    "sequence": 1234,                           │
     │    "timestamp": "ISO8601",                      │
     │    "events": [...]                              │
     │  }                                              │
     │ ──────────────────────────────────────────────► │
     │                                                  │
     │                                                  │  4. Process events
     │                                                  │  5. Emit to event bus
     │                                                  │
     │  (no response needed - QoS 1 guarantees)        │
```

### Event Batch Schema

```python
class MinionEventBatch(BaseModel):
    minion_id: UUID
    sequence: int                    # Monotonic counter per minion
    timestamp: datetime             # When batch was created
    events: list[MinionEvent]

class MinionEvent(BaseModel):
    type: str                        # "location", "payment", etc.
    occurred_at: datetime            # When event happened (device time)
    received_at: datetime | None    # Set by Cortex on receipt
    payload: dict                    # Event-specific data
    accuracy: float | None = None   # Sensor confidence
    filtered: bool = False          # True if minion filtered this

class LocationEventPayload(BaseModel):
    latitude: float
    longitude: float
    altitude: float | None
    accuracy: float                 # meters
    speed: float | None            # m/s
    heading: float | None          # degrees

class PaymentEventPayload(BaseModel):
    amount: Decimal
    currency: str                   # ISO 4217
    merchant: str
    category: str                   # "restaurant", "grocery"
    card_last_four: str | None
    transaction_type: Literal["purchase", "refund", "withdrawal"]
```

---

## Heartbeat

### Purpose

- Detect minion connectivity
- Allow Cortex to push config updates
- Provide backchannel for commands

### Heartbeat Message (Minion → Cortex)

```
Topic: cortex/minions/<minion_id>/heartbeat
QoS: 0 (fire-and-forget)
Payload:
{
  "timestamp": "ISO8601",
  "status": "healthy",
  "battery_level": 0.85,
  "network_type": "wifi",
  "queue_size": 0,                  # Events queued locally
  "last_sequence": 1234,            # For gap detection
  "stats": {
    "events_sent": 150,
    "events_failed": 0,
    "uptime_seconds": 3600
  }
}
```

### Command Message (Cortex → Minion)

```
Topic: cortex/minions/<minion_id>/commands/config
QoS: 1
Payload:
{
  "command_id": "uuid",
  "command": "update_config",
  "config": {
    "sampling_interval": 300
  }
}
```

---

## Offline Handling

### MQTT Broker Persistence

| Setting | Value |
|---------|-------|
| QoS for events | 1 (at-least-once) |
| Clean session | false (persistent session) |
| Message expiry | 24 hours |
| Max queued messages | 1000 per topic |

### Minion Behavior

| Scenario | Behavior |
|----------|----------|
| Connected | Publish immediately |
| Disconnected | Queue locally (up to max_queue_size) |
| Reconnected | Flush queue, resume from last sequence |
| Queue full | Drop oldest, log warning |

---

## Configuration Push

### Config Delivery

```
1. Cortex updates config in DB
2. Cortex publishes to:
   Topic: cortex/minions/<minion_id>/commands/config
3. Minion receives, applies, acknowledges
```

### Config Schema

```python
class MinionConfig(BaseModel):
    version: int
    sensors: dict[str, SensorConfig]
    batch: BatchConfig
    privacy: PrivacyConfig

class SensorConfig(BaseModel):
    enabled: bool
    sampling_interval: int          # seconds
    significant_change: float | None # meters for location
    debounce_seconds: int | None

class BatchConfig(BaseModel):
    max_size: int                   # events per batch
    flush_interval: int             # seconds

class PrivacyConfig(BaseModel):
    exclude_apps: list[str] = []
    exclude_locations: list[str] = []
    precision_reduction: str | None # "city" | "neighborhood" | "precise"
```

---

## Error Handling

### MQTT Connection Errors

| Scenario | Action |
|----------|--------|
| Invalid credentials | Log error, prompt user to regenerate token |
| Connection refused | Retry with backoff (5s, 10s, 30s, 60s, max 5min) |
| Broker unreachable | Queue locally, retry when network available |

### Minion-Side Retry Strategy

```
Initial retry: 5 seconds
Max retry: 5 minutes
Max retries: unlimited (keep trying forever)
Jitter: ±20%
```

### Sequence Gap Handling

| Gap Size | Action |
|----------|--------|
| 1-10 | Log warning, request retransmit (future feature) |
| >10 | Log warning, accept gap, continue |
| Always starts at 1 | Minion reprovisioning required |

---

## Broker Configuration (Mosquitto)

```conf
# mosquitto.conf

# Listener for Minions (plain MQTT)
listener 1883
protocol mqtt

# Authentication
allow_anonymous false
password_file /mosquitto/config/passwd.conf

# Persistence
persistence true
persistence_location /mosquitto/data/
persistence_file mosquitto.db

# Message persistence for offline minions
max_queued_messages 1000
message_expiry_interval 86400

# Allow existing clients to finish
persistent_client_expiration 1h

# Logging
log_dest file /mosquitto/log/mosquitto.log
log_dest stdout
log_type error
log_type warning
log_type notice
log_type information
```

### Password File Format

```
# mosquitto/config/passwd.conf
# Format: username:password (bcrypt hashed)
minion_abc123def:$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/X4tQQ...
minion_def456ghi:$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj...
```

Generate passwords with:
```bash
mosquitto_passwd -U /mosquitto/config/passwd.conf  # Update to bcrypt
```

---

## Project Structure

```
cortex/
├── src/cortex/
│   ├── minion_api/                    # Cortex-side minion handling
│   │   ├── __init__.py
│   │   ├── routes.py                 # HTTP endpoints (admin UI)
│   │   ├── mqtt_client.py            # Cortex MQTT subscriber
│   │   ├── auth.py                   # Token management
│   │   ├── event_handler.py          # Parse, validate, emit to bus
│   │   └── models.py                 # Pydantic schemas
│   │
│   ├── minion_impl/                  # Minion implementations
│   │   ├── phone/                    # Phone minion (Python CLI)
│   │   │   ├── __init__.py
│   │   │   ├── mqtt_client.py
│   │   │   ├── sensors/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── location.py
│   │   │   │   └── calendar.py
│   │   │   └── main.py
│   │   │
│   │   ├── card/                     # Card minion (future)
│   │   │   └── ...
│   │   │
│   │   └── laptop/                   # Laptop minion (future)
│   │       └── ...
│   │
│   └── minion_config.yaml            # Example minion config

mosquitto/
├── Dockerfile
└── config/
    ├── mosquitto.conf
    └── passwd.conf                   # Generated from Cortex

docker-compose.yml
```

---

## Security Summary

| Threat | Mitigation |
|--------|------------|
| Minion impersonation | API token authentication (MQTT username/password) |
| Unauthorized access | Token stored in minion config, user-managed |
| Replay attacks | Sequence numbers in batches |
| Data at rest (broker) | Broker runs on trusted local network |
| Network sniffing | v1: no encryption (trusted network); v2: TLS |

### v1 Security Model

```
┌─────────────────────────────────────────────────────────────────┐
│                     TRUST BOUNDARY                              │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              Local Network (Home/WiFi)                   │   │
│  │                                                          │   │
│  │   ┌──────────┐    ┌──────────┐    ┌──────────┐        │   │
│  │   │  Phone   │    │  Laptop  │    │  Cortex  │        │   │
│  │   │  Minion  │    │  Minion  │    │  + Broker│        │   │
│  │   └──────────┘    └──────────┘    └──────────┘        │   │
│  │                                                          │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                  │
│  Outside: Untrusted network (internet)                         │
│  └── User accesses Cortex UI via HTTPS                         │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**Assumption:** All minions run on the same trusted network as Cortex.

---

## Future: Adding TLS (v2)

When minions need to connect over untrusted networks:

| Step | Action |
|------|--------|
| 1 | Add TLS to MQTT listener (port 8883) |
| 2 | Use self-signed server cert or Let's Encrypt |
| 3 | Keep API tokens for authentication (no mTLS) |
| 4 | Minion validates server cert, sends token |

This gives you TLS encryption without the complexity of mTLS client certificates.

---

## API Reference

### MQTT Topics

| Topic | Direction | QoS | Purpose |
|-------|-----------|-----|---------|
| `cortex/minions/<id>/register` | → Cortex | 1 | Registration |
| `cortex/minions/<id>/events` | → Cortex | 1 | Event batch |
| `cortex/minions/<id>/heartbeat` | → Cortex | 0 | Heartbeat/status |
| `cortex/minions/<id>/commands/*` | ← Cortex | 1 | Commands from Cortex |

### HTTP Endpoints (Admin UI)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `GET /admin/minions` | GET | List registered minions |
| `GET /admin/minions/<id>` | GET | Minion details + status |
| `POST /admin/minions/<id>/token` | POST | Generate new API token |
| `DELETE /admin/minions/<id>/token` | POST | Revoke API token |
| `POST /admin/minions/<id>/config` | POST | Push config update |

---

## Open Questions

- [x] TLS vs E2E encryption → **No TLS for v1 (trusted network)**; TLS for v2
- [x] One-way auth vs mTLS → **API tokens (no certificates)**
- [x] HTTPS vs MQTT → **MQTT**
- [x] SDK vs standalone → **No SDK**
- [x] Push vs Pull → **Push**
- [x] Token provisioning → **User setup** (user generates token in Cortex UI, copies to minion config)
- [x] Token rotation → **Manual** (user regenerates from UI)
- [x] Broker hosting → **Self-hosted** (Mosquitto in Docker)

---

## Status

✅ Protocol design complete — ready for implementation

---

*Last updated: 2026-04-29*
