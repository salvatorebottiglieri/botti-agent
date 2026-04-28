# Minion Protocol Design

> How minions communicate with Cortex. Design for v1.
> Created: 2026-04-28

---

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         MINION                                   │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐ │
│  │ Data         │→ │ Filter &     │→ │ MQTT Client            │ │
│  │ Collectors   │  │ Normalizer   │  │ (TLS + mTLS auth)      │ │
│  │ (GPS, API)   │  │ (debounce,    │  │                        │ │
│  │              │  │  dedup)       │  │                        │ │
│  └──────────────┘  └──────────────┘  └───────────┬────────────┘ │
└─────────────────────────────────────────────────┼───────────────┘
                                                  │ MQTT over TLS
                                                  ▼
                                        ┌─────────────────┐
                                        │   MQTT Broker   │
                                        │   (TLS + mTLS)  │
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

---

## Transport: MQTT over TLS

| Aspect | Decision |
|--------|----------|
| Broker | Eclipse Mosquitto or EMQX |
| Protocol | MQTT 5.0 |
| Transport | TLS 1.3 (wss:// for WebSocket fallback) |
| QoS | QoS 1 (at-least-once delivery) |
| Persistence | Broker-based (broker stores messages for offline minions) |

### Topic Structure

```
cortex/
├── minions/
│   └── <minion_id>/
│       ├── events              # Minion → Cortex (events)
│       ├── heartbeat           # Minion → Cortex (heartbeat)
│       └── commands/           # Cortex → Minion (future)
│           └── config          # Cortex → Minion (config updates)
```

### Why MQTT?

| Benefit | Impact |
|---------|--------|
| Persistent connection | Lower latency, no connection overhead |
| QoS guarantees | Reliable event delivery |
| Offline buffering | Broker stores events while minion is offline |
| Keep-alive built-in | Automatic dead connection detection |
| Battery efficient | Long-lived TCP connection, minimal overhead |
| Bidirectional | Easy to add Cortex→Minion commands later |

---

## Authentication: Mutual TLS (mTLS)

### Why mTLS?

| Aspect | Benefit |
|--------|---------|
| **Authenticity** | Both parties verify identity via certificates |
| **Integrity** | All traffic encrypted and tamper-proof |
| **No passwords** | No API keys to manage, rotate, or leak |
| **Forward secrecy** | TLS 1.3 with ephemeral keys |

### Certificate Model

```
┌─────────────────────────────────────────────────────────────────┐
│                      CERTIFICATE HIERARCHY                      │
│                                                                  │
│                        Root CA                                  │
│                   (Self-signed, long-lived)                      │
│                         │                                       │
│            ┌────────────┴────────────┐                          │
│            ▼                         ▼                          │
│     Intermediate CA            Intermediate CA                  │
│       (Cortex side)             (Minion side)                   │
│            │                         │                          │
│            ▼                         ▼                          │
│     cortex-server cert         minion-client cert               │
│     (server auth)              (client auth)                    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Certificate Management

| Certificate | Issuer | Validity | Storage |
|-------------|--------|----------|---------|
| Root CA | Self-signed | 10 years | Pre-installed |
| Cortex Intermediate CA | Root CA | 5 years | Deployed with Cortex |
| Minion Intermediate CA | Root CA | 5 years | Pre-installed in minion |
| Cortex Server Cert | Cortex Intermediate CA | 1 year | Deployed with Cortex |
| Minion Client Cert | Minion Intermediate CA | 1 year | Generated at minion provisioning |

### Minion Certificate Contents

```
Subject:
  CN: minion-<minion_id>
  O: Cortex
  OU: Minions

X509v3 Extensions:
  Extended Key Usage: TLS Web Client Authentication
  Key Usage: Digital Signature
  Subject Alternative Name: 
    - URI:urn:minion:<minion_id>
```

### Cortex Certificate Contents

```
Subject:
  CN: cortex.example.com (or hostname)
  O: Cortex
  OU: Server

X509v3 Extensions:
  Extended Key Usage: TLS Web Server Authentication
  Key Usage: Digital Signature, Key Encipherment
```

---

## Registration Flow

> Minion obtains its client certificate during provisioning (factory-style).

### Pre-requisites

```
Minion provisioned with:
├── Root CA certificate (trust store)
├── Minion Intermediate CA certificate
├── Minion private key
└── Minion client certificate (signed by Minion CA)
```

### Initial Connection

```
┌──────────┐                           ┌──────────┐
│  Minion  │                           │  Cortex  │
└────┬─────┘                           └────┬─────┘
     │                                      │
     │  1. TLS handshake (mTLS)             │
     │     Client cert presented            │
     │ ──────────────────────────────────► │
     │                                      │
     │                                      │  2. Verify client cert
     │                                      │     - Signed by trusted CA?
     │                                      │     - Not expired/revoked?
     │                                      │     - CN matches minion_id?
     │                                      │
     │  200 OK                              │
     │  {                                   │
     │    registered: true,                │
     │    heartbeat_interval: 300,         │
     │    batch_size: 50,                   │
     │    mqtt_topic_prefix: "cortex"      │
     │  }                                   │
     │ ◄──────────────────────────────────── │
     │                                      │
     │  3. Subscribe to commands topic      │
     │     cortex/minions/<minion_id>/commands/#
     │                                      │
     │  4. Begin publishing events          │
```

### Registration Request (over MQTT)

```
Topic: cortex/minions/<minion_id>/register
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

### Registration Response

```
Topic: cortex/minions/<minion_id>/register/response
Payload:
{
  "status": "registered",
  "heartbeat_interval": 300,
  "batch_size": 50,
  "supported_event_types": ["location", "payment", "activity"],
  "max_payload_bytes": 65536
}
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

### Command Message (Cortex → Minion) — Future

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

### MQTT Error Codes

| Code | Meaning | Action |
|------|---------|--------|
| 0x00 | Connection successful | - |
| 0x01 | Unacceptable protocol | Reconnect with different protocol |
| 0x02 | Identifier rejected | Check minion_id, regenerate if needed |
| 0x03 | Server unavailable | Retry with backoff |
| 0x04 | Bad credentials | Re-register |
| 0x05 | Not authorized | Check certificate validity |

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
| 1-10 | Request retransmit via command channel |
| >10 | Log warning, accept gap, continue |
| Always starts at 1 | Minion reprovisioning required |

---

## Broker Configuration (Mosquitto)

```conf
# mosquitto.conf

# TLS Configuration
listener 8883
cafile /certs/ca.crt
certfile /certs/server.crt
keyfile /certs/server.key
require_certificate true
use_identity_as_username true

# Persistence
persistence true
persistence_location /mosquitto/data/
max_queued_messages 1000
message_expiry_interval 86400

# Security
allow_anonymous false
```

---

## Project Structure

```
cortex/
├── src/cortex/
│   ├── minion_api/                    # Cortex-side minion handling
│   │   ├── __init__.py
│   │   ├── routes.py                 # HTTP endpoints (for admin/debug)
│   │   ├── mqtt_client.py            # Cortex MQTT subscriber
│   │   ├── cert_manager.py           # Certificate validation
│   │   ├── event_handler.py          # Parse, validate, emit to bus
│   │   └── models.py                 # Pydantic schemas
│   │
│   ├── minion_impl/                  # Minion implementations (no SDK)
│   │   ├── phone/                    # Phone minion (Python CLI / mobile)
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
│   └── certs/                        # Certificate management
│       ├── ca/                       # CA certificates
│       └── scripts/                 # Provisioning scripts
│           ├── generate_ca.py
│           ├── generate_minion_cert.py
│           └── revoke_cert.py

mosquitto/
├── Dockerfile
└── config/mosquitto.conf

docker-compose.yml
```

---

## Security Summary

| Threat | Mitigation |
|--------|------------|
| Minion impersonation | mTLS client certificates |
| Server impersonation | mTLS server certificate + CA trust |
| Eavesdropping | TLS 1.3 encryption |
| Tampering | TLS integrity checks |
| Replay | Sequence numbers in batches |
| Data at rest (broker) | Broker runs on trusted infrastructure |
| Certificate revocation | CRL/OCSP checking (future) |

---

## Scalability Path

| Phase | What's Needed |
|-------|---------------|
| v1 | Single broker, single Cortex instance |
| v2 | Broker cluster (EMQX), Cortex scales horizontally |
| v2 | Certificate automation (Let's Encrypt / Vault PKI) |
| v3 | Multi-region brokers with federation |

---

## API Reference

### MQTT Topics

| Topic | Direction | QoS | Purpose |
|-------|-----------|-----|---------|
| `cortex/minions/<id>/register` | → Cortex | 1 | Registration |
| `cortex/minions/<id>/register/response` | ← Cortex | 1 | Registration response |
| `cortex/minions/<id>/events` | → Cortex | 1 | Event batch |
| `cortex/minions/<id>/heartbeat` | → Cortex | 0 | Heartbeat/status |
| `cortex/minions/<id>/commands/config` | ← Cortex | 1 | Config push (future) |

### HTTP Endpoints (Admin/Debug)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `GET /admin/minions` | GET | List registered minions |
| `GET /admin/minions/<id>` | GET | Minion details + status |
| `POST /admin/minions/<id>/revoke` | POST | Revoke minion certificate |
| `POST /admin/minions/<id>/config` | POST | Push config update |

---

## Open Questions

- [x] TLS vs E2E encryption → **TLS only**
- [x] One-way auth vs mTLS → **mTLS (two-way)**
- [x] HTTPS vs MQTT → **MQTT**
- [x] SDK vs standalone → **No SDK**
- [x] Push vs Pull → **Push**
- [x] Certificate provisioning → **User setup** (user initiates via Cortex UI/CLI)
- [x] Certificate rotation → **Automated**
- [x] Broker hosting → **Self-hosted** (Mosquitto/EMQX in Docker)

---

## Status

✅ Protocol design complete — ready for implementation

---

## Certificate Provisioning (User Setup)

### Overview

User initiates minion setup via Cortex UI or CLI. Minion generates key pair locally, user approves in Cortex, Cortex issues certificate.

### Certificate Hierarchy (Simplified for User Setup)

```
┌─────────────────────────────────────────────────────────────────┐
│                    CASTRATION (Cortex CA)                       │
│                                                                  │
│  Self-signed Root CA                                            │
│  ├── Installed on: Cortex server                                │
│  ├── Installed on: All user minions (baked into app)           │
│  └── Validity: 10 years                                         │
│                                                                  │
│  Signs: Minion certificates (per-device)                       │
│  ├── Validity: 30 days                                          │
│  └── Renewal: Automated, before expiry                         │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**Note:** "CASTRATION" = Cortex's internal CA service for minion certificates.

### Setup Flow

```
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│  User    │     │  Minion   │     │  Cortex  │     │  Broker  │
│          │     │  (App)    │     │  (Server)│     │(Mosquitto)│
└────┬─────┘     └────┬─────┘     └────┬─────┘     └────┬─────┘
     │                 │                 │                 │
     │ 1. User starts  │                 │                 │
     │    setup in app  │                 │                 │
     │ ───────────────► │                 │                 │
     │                 │                 │                 │
     │                 │ 2. Generate     │                 │
     │                 │    key pair     │                 │
     │                 │    (P-256 ECDSA)│                 │
     │                 │                 │                 │
     │ 3. Show QR code  │                 │                 │
     │    or pairing    │                 │                 │
     │    token         │                 │                 │
     │ ◄─────────────── │                 │                 │
     │                 │                 │                 │
     │ 4. Scan QR /     │                 │                 │
     │    enter token   │                 │                 │
     │    in Cortex UI  │                 │                 │
     │ ──────────────────────────────────────────────────────►
     │                 │                 │                 │
     │                 │                 │ 5. Verify token  │
     │                 │                 │ 6. Create         │
     │                 │                 │    minion record  │
     │                 │                 │ 7. Issue cert     │
     │                 │                 │    (signed by    │
     │                 │                 │    CASTRATION)   │
     │                 │                 │                 │
     │ 8. Cert ready    │                 │                 │
     │ ◄─────────────── │                 │                 │
     │                 │                 │                 │
     │ 9. Download cert │                 │                 │
     │    (or auto-     │                 │                 │
     │     transfer)    │                 │                 │
     │ ────────────────► │                 │                 │
     │                 │                 │                 │
     │                 │10. Store cert    │                 │
     │                 │   + root CA     │                 │
     │                 │   + broker URL  │                 │
     │                 │                 │                 │
     │                 │11. Connect to   │                 │
     │                 │    broker (mTLS)│                 │
     │                 │ ───────────────────────────────────►
     │                 │                 │                 │
     │                 │                 │12. TLS + mTLS   │
     │                 │                 │    handshake    │
     │                 │                 │ ◄───────────────
     │                 │                 │                 │
     │                 │13. Subscribe to │                 │
     │                 │    commands     │                 │
     │                 │14. Publish     │                 │
     │                 │    register     │                 │
     │                 │ ───────────────►│                 │
     │                 │                 │                 │
     │                 │                 │15. Confirm reg  │
     │                 │ ◄───────────────│                 │
     │                 │                 │                 │
     │16. Show success  │                 │                 │
     │ ◄─────────────── │                 │                 │
```

### Provisioning Details

#### Step 1-2: Key Generation (Minion)

```python
# Minion generates locally
private_key = ec.generate_private_key(ec.SECP256R1())
csr = generate_csr(
    private_key=private_key,
    common_name=f"minion-{minion_id}",
    organization="Cortex",
    organizational_unit="Minions"
)
# CSR is displayed to user as QR code
```

#### Step 3-5: User Approval (Cortex)

```
User opens Cortex UI:
├── Enters pairing token (from minion QR)
├── Assigns friendly name (e.g., "Sarah's Phone")
├── Reviews minion type and capabilities
└── Clicks "Approve"

Cortex:
├── Validates pairing token (not expired, not used)
├── Extracts minion_id from CSR
├── Creates Minion record in DB
├── Generates certificate (signs CSR)
└── Stores cert for retrieval
```

#### Step 6: Certificate Format

```python
class MinionCertificate:
    # X.509 fields
    subject: CN = f"minion-{minion_id}"
    subject: O = "Cortex"
    subject: OU = "Minions"
    
    # Extensions
    key_usage: [digital_signature]
    ext_key_usage: [client_auth]
    
    # Custom extensions
    custom_minion_id: str = minion_id
    custom_minion_type: str = "phone"
    
    # Validity
    not_before: datetime
    not_after: datetime = not_before + 30 days
```

#### Pairing Token Format

```
token = base64url({
    "minion_id": "uuid",           # Pre-generated by minion
    "csr": "base64-encoded-csr",   # Certificate signing request
    "created_at": "ISO8601",        # For expiry (15 min)
    "signature": "HMAC(challenge)"  # Proves minion possesses private key
})
```

---

## Certificate Rotation (Automated)

### Overview

Minion certificates have short validity (30 days). Minion automatically renews before expiry with zero downtime.

### Certificate Lifetimes

| Certificate | Validity | Rotation |
|-------------|----------|----------|
| Root CA (CASTRATION) | 10 years | Manual |
| Minion certificate | 30 days | Automated |

### Renewal Flow

```
┌──────────┐     ┌──────────┐     ┌──────────┐
│  Minion  │     │  Cortex  │     │   DB     │
└────┬─────┘     └────┬─────┘     └────┬─────┘
     │                 │                 │
     │ 1. Check: cert  │                 │
     │    expires in   │                 │
     │    < 7 days?    │                 │
     │                 │                 │
     │ 2. Generate new  │                 │
     │    key pair     │                 │
     │                 │                 │
     │ 3. MQTT: renew   │                 │
     │    request       │                 │
     │ ───────────────► │                 │
     │                 │                 │
     │                 │ 4. Validate      │
     │                 │    - Old cert   │
     │                 │      still valid│
     │                 │    - New key   │
     │                 │      matches   │
     │                 │    - minion_id │
     │                 │      matches   │
     │                 │                 │
     │                 │ 5. Revoke old   │
     │                 │    cert (CRL)  │
     │                 │ 6. Issue new   │
     │                 │    cert        │
     │                 │                 │
     │ 7. MQTT: renew  │                 │
     │    response     │                 │
     │    (new cert)   │                 │
     │ ◄────────────── │                 │
     │                 │                 │
     │ 8. Install new  │                 │
     │    cert         │                 │
     │    (atomic)     │                 │
     │                 │                 │
     │ 9. Reconnect    │                 │
     │    with new     │                 │
     │    cert         │                 │
```

### Renewal Request/Response

```python
# Renewal Request (Minion → Cortex)
class CertificateRenewalRequest:
    topic: "cortex/minions/<minion_id>/commands/renew"
    
    current_cert_fingerprint: str     # SHA-256 of current cert
    new_csr: str                      # Base64-encoded CSR
    new_public_key: str               # Base64-encoded new public key

# Renewal Response (Cortex → Minion)
class CertificateRenewalResponse:
    topic: "cortex/minions/<minion_id>/commands/renew/response"
    
    status: "issued" | "pending" | "denied"
    new_cert: str | None             # PEM-encoded cert if issued
    new_cert_valid_until: datetime   # For countdown
    retry_after: int | None          # Seconds if pending

# Denial Response
class CertificateRenewalDenied:
    status: "denied"
    reason: str                       # "key_reused", "suspicious", etc.
```

### Key Reuse Prevention

```python
# Cortex stores hash of all public keys ever used
class UsedPublicKey:
    minion_id: UUID
    key_fingerprint: str              # SHA-256 of public key
    used_at: datetime

# On renewal request:
if new_public_key_fingerprint in used_keys[minion_id]:
    deny("key_reused")
```

**Rationale:** Prevents someone extracting the old private key and using it to get a new cert.

### Graceful Transition

```
Day 1-23:  Minion uses current cert
Day 24-30: Minion renews, gets new cert
          Minion tries new cert first
          Falls back to old cert if new cert rejected
Day 30:    Old cert expires
          Minion must have new cert installed
Day 30+:   If no valid cert, minion is offline
          User notified via Cortex UI
```

### Emergency Renewal

If renewal fails repeatedly:

```python
class EmergencyRenewalRequest:
    reason: "renewal_failed" | "cert_lost" | "key_compromised"
    proof_of_identity: str           # User-provided token from Cortex UI
```

User can generate an emergency token from Cortex UI that allows one-time certificate re-issue without valid existing cert.

---

## Self-Hosted Broker

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        USER'S INFRASTRUCTURE                     │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                     Docker Compose                          ││
│  │                                                              ││
│  │   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐     ││
│  │   │   Cortex    │    │   Broker    │    │  Postgres   │     ││
│  │   │   (API)     │◄──►│ (Mosquitto) │    │   (DB)      │     ││
│  │   │             │    │             │    │             │     ││
│  │   │  Port 8000  │    │  Port 8883  │    │   Port 5432 │     ││
│  │   │  (HTTPS)    │    │   (mTLS)    │    │             │     ││
│  │   └─────────────┘    └──────┬──────┘    └─────────────┘     ││
│  │                              │                             ││
│  │                     ┌─────────┴─────────┐                    ││
│  │                     │   Shared Network  │                    ││
│  │                     │   (bridge)        │                    ││
│  │                     └───────────────────┘                    ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Docker Compose

```yaml
version: '3.8'

services:
  # ─── MQTT Broker ───
  broker:
    image: eclipse-mosquitto:2
    container_name: cortex-broker
    restart: unless-stopped
    ports:
      - "8883:8883"           # MQTT over TLS
    volumes:
      - ./mosquitto/config:/mosquitto/config
      - ./mosquitto/data:/mosquitto/data
      - ./mosquitto/log:/mosquitto/log
      - ./certs:/certs:ro     # Certificates (read-only)
    command: mosquitto -c /mosquitto/config/mosquitto.conf
    networks:
      - cortex-net

  # ─── Cortex API ───
  cortex:
    image: cortex/api:latest
    container_name: cortex-api
    restart: unless-stopped
    ports:
      - "8000:8000"           # HTTP API
    volumes:
      - ./cortex/config.yaml:/app/config.yaml:ro
      - ./certs/client:/app/certs  # Minion client certs
    depends_on:
      - postgres
      - broker
    environment:
      - POSTGRES_HOST=postgres
      - MQTT_BROKER_URL=mqtts://broker:8883
    networks:
      - cortex-net

  # ─── Postgres ───
  postgres:
    image: postgres:15-alpine
    container_name: cortex-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: cortex
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: cortex
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - cortex-net

volumes:
  postgres_data:

networks:
  cortex-net:
    driver: bridge
```

### Mosquitto Configuration

```conf
# mosquitto/config/mosquitto.conf

# Listener for Minions (mTLS)
listener 8883
protocol mqtt

# TLS Configuration
cafile /certs/castration.crt
certfile /certs/broker.crt
keyfile /certs/broker.key

# Require client certificates
require_certificate true

# Use CN from client cert as username
use_identity_as_username true

# Verify client cert against our CA
verify_certificate true
verify_certificate_depth 2

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

# Security
allow_anonymous false
```

### Certificate Files Structure

```
./certs/
├── ca/
│   ├── castration.crt         # Root CA certificate (ships with minion apps)
│   ├── castration.key          # Root CA private key (Cortex only)
│   │
│   └── minion/                 # Minion certs (managed by Cortex)
│       ├── minion-abc123.crt
│       ├── minion-def456.crt
│       └── ...
│
├── broker/
│   ├── broker.crt              # Broker server certificate
│   └── broker.key              # Broker private key
│
└── client/                    # Client certs for Cortex→Broker (if needed)
    ├── cortex.crt
    └── cortex.key
```

### Firewall Considerations

| Port | Service | Access | Purpose |
|------|---------|--------|---------|
| 8883 | MQTT/TLS | Minions (internet) | Minion connections |
| 8000 | HTTPS | User (browser) | Cortex UI + API |

**For home users:** Configure port forwarding on router.

**For security:** Use dynamic DNS + Let's Encrypt for Cortex UI certificate.

---

*Last updated: 2026-04-28*
