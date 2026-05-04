# Minion Event Schemas

> Structured event payloads for each minion type. Design for v1.
> Created: 2026-04-28

---

## Overview

Minions emit events via MQTT. Each event has:
- `type` — what happened
- `occurred_at` — when it happened (device time)
- `payload` — structured data

Events are batched by the minion before sending.

---

## Phone Minion Events

### 1. Location Event

> GPS coordinates from phone.

```python
class LocationEvent(BaseModel):
    type: Literal["location"] = "location"
    occurred_at: datetime
    payload: LocationPayload

class LocationPayload(BaseModel):
    # Coordinates
    latitude: float          # WGS84, -90 to 90
    longitude: float         # WGS84, -180 to 180
    altitude: float | None  # Meters above sea level
    
    # Accuracy
    accuracy: float          # Meters (68% confidence radius)
    altitude_accuracy: float | None  # Meters (if available)
    
    # Motion
    speed: float | None      # Meters per second
    heading: float | None    # Degrees, 0-360, 0=North
    
    # Source info
    source: Literal["gps", "network", "fused"] = "gps"
    provider: str | None     # e.g., "android.location.GPS_PROVIDER"
    
    # Derived (optional, Cortex can derive these)
    speed_category: Literal["stationary", "walking", "running", "cycling", "driving"] | None = None
```

**Filtering rules (minion-side):**
| Condition | Action |
|-----------|--------|
| Accuracy > 100m | Include but flag low accuracy |
| Speed > 50 m/s | Discard (GPS glitch) |
| Same location < 60s | Debounce |
| Location unchanged < 100m | Debounce |

---

### 2. Activity Event

> Detected physical activity.

```python
class ActivityEvent(BaseModel):
    type: Literal["activity"] = "activity"
    occurred_at: datetime
    payload: ActivityPayload

class ActivityPayload(BaseModel):
    activity_type: ActivityType
    confidence: float                    # 0.0-1.0
    
    # Duration
    start_time: datetime
    duration_seconds: int
    
    # Supporting activities (Android ActivityRecognition returns multiple)
    supporting_activities: list[ActivityType] = []
    supporting_confidences: list[float] = []

class ActivityType(str, Enum):
    IN_VEHICLE = "in_vehicle"         # Car, bus, train
    ON_BICYCLE = "on_bicycle"        # Cycling
    ON_FOOT = "on_foot"              # Walking or running
    RUNNING = "running"
    WALKING = "walking"
    STILL = "still"                  # Not moving
    UNKNOWN = "unknown"
```

**When to emit:**
- Activity change detected (transitions between states)
- Periodic update every 5 minutes while active
- Significant duration change (> 30 seconds)

---

### 3. Calendar Event

> Upcoming calendar entry.

```python
class CalendarEvent(BaseModel):
    type: Literal["calendar"] = "calendar"
    occurred_at: datetime
    payload: CalendarPayload

class CalendarPayload(BaseModel):
    # Identity
    calendar_id: str                # Source calendar ID
    event_id: str                  # Unique event ID
    
    # Timing
    title: str
    start_time: datetime
    end_time: datetime
    all_day: bool = False
    timezone: str                  # IANA timezone
    
    # Location
    location: str | None
    latitude: float | None
    longitude: float | None
    
    # People
    attendees: list[Attendee] = []
    organizer: str | None
    
    # Classification
    event_type: Literal["meeting", "reminder", "task", "out_of_office", "other"] = "other"
    
    # Status
    status: Literal["confirmed", "tentative", "cancelled"] = "confirmed"
    
    # Recurrence
    recurrence: str | None         # RRULE string if recurring

class Attendee(BaseModel):
    name: str | None
    email: str | None
    status: Literal["accepted", "declined", "tentative", "needs_action"] = "needs_action"
```

**When to emit:**
- New event created/modified/deleted
- 15 minutes before event start (reminder)
- Event started (entering location)

**Privacy note:** Only sync events user has explicitly allowed. Minion respects per-calendar permissions.

---

### 4. App Usage Event

> Application usage summary.

```python
class AppUsageEvent(BaseModel):
    type: Literal["app_usage"] = "app_usage"
    occurred_at: datetime
    payload: AppUsagePayload

class AppUsagePayload(BaseModel):
    # Time window
    window_start: datetime
    window_end: datetime
    duration_seconds: int
    
    # App info (whitelisted apps only)
    package_name: str              # e.g., "com.whatsapp"
    app_name: str | None           # Human-readable name
    
    # Usage type
    usage_type: UsageType
    
    # Foreground duration
    foreground_duration_seconds: int

class UsageType(str, Enum):
    FOREGROUND = "foreground"       # App in foreground
    BACKGROUND = "background"       # App running in background
    SYSTEM = "system"              # System interaction
    
# Privacy: Only whitelisted apps are reported
# Default whitelist: messaging, email, calendar, maps, transportation
```

**When to emit:**
- Periodic summary (every 15 minutes)
- App switched to foreground (app change event)

**Privacy note:** Never report app usage for apps user hasn't explicitly whitelisted. Social media, dating apps, etc. excluded by default.

---

### 5. Call Log Event

> Incoming/outgoing phone call.

```python
class CallLogEvent(BaseModel):
    type: Literal["call_log"] = "call_log"
    occurred_at: datetime
    payload: CallLogPayload

class CallLogPayload(BaseModel):
    # Call details
    direction: Literal["incoming", "outgoing", "missed", "rejected", "blocked"]
    duration_seconds: int
    
    # Contact (anonymized by default)
    contact_name: str | None        # "John Doe" or null if unknown
    phone_number: str | None        # Always null unless user explicitly allows
    
    # Phone number hash (for matching across devices, optional)
    phone_number_hash: str | None   # SHA-256 hash
    
    # Whether answered
    answered: bool
    
    # SIM slot (for multi-SIM phones)
    sim_slot: int = 0
```

**When to emit:**
- Call ended (for incoming, outgoing, missed)
- Immediately for rejected/blocked

**Privacy note:** Phone numbers are never transmitted unless user explicitly opts in. Call patterns (frequency, duration, time of day) are still useful for learning without exposing numbers.

---

## Card Minion Events

### 6. Payment Event

> Card transaction.

```python
class PaymentEvent(BaseModel):
    type: Literal["payment"] = "payment"
    occurred_at: datetime
    payload: PaymentPayload

class PaymentPayload(BaseModel):
    # Transaction
    transaction_id: str             # Unique network transaction ID
    amount: Decimal                 # Always positive
    currency: str                   # ISO 4217 (e.g., "EUR", "USD")
    
    # Merchant
    merchant_name: str
    merchant_category: MerchantCategory
    merchant_category_code: str     # ISO 18245 MCC code
    merchant_city: str | None
    merchant_country: str | None   # ISO 3166-1 alpha-2
    
    # Location (if available)
    latitude: float | None
    longitude: float | None
    
    # Card details (masked)
    card_last_four: str             # Last 4 digits only
    card_type: Literal["credit", "debit"] | None
    
    # Transaction type
    transaction_type: TransactionType
    
    # Status
    status: Literal["completed", "pending", "declined", "refunded", "reversed"]
    
    # Refund info
    refunded_amount: Decimal | None
    original_transaction_id: str | None

class MerchantCategory(str, Enum):
    GROCERIES = "groceries"
    RESTAURANT = "restaurant"
    CAFE = "cafe"
    BAR = "bar"
    TRANSPORT = "transport"
    FUEL = "fuel"
    SHOPPING = "shopping"
    ENTERTAINMENT = "entertainment"
    HEALTH = "health"
    TRAVEL = "travel"
    ACCOMMODATION = "accommodation"
    SERVICES = "services"           # Hairdresser, etc.
    UTILITIES = "utilities"
    OTHER = "other"

class TransactionType(str, Enum):
    PURCHASE = "purchase"
    REFUND = "refund"
    WITHDRAWAL = "withdrawal"
    FEE = "fee"
    TRANSFER = "transfer"
```

**When to emit:**
- Transaction authorization
- Transaction settlement (may differ from auth)
- Refund initiated

**Privacy note:** Exact merchant names + amounts are sensitive. Cortex stores these encrypted. User can configure to only receive category-level data.

---

### 7. Refund Event

> A refund to the card.

```python
class RefundEvent(BaseModel):
    type: Literal["refund"] = "refund"
    occurred_at: datetime
    payload: RefundPayload

class RefundPayload(BaseModel):
    refund_id: str
    original_transaction_id: str
    amount: Decimal
    currency: str
    merchant_name: str | None
    status: Literal["initiated", "completed", "failed"]
    initiated_at: datetime
    completed_at: datetime | None
```

---

## Laptop Minion Events

### 8. Screen Activity Event

> Screen on/off and active window.

```python
class ScreenActivityEvent(BaseModel):
    type: Literal["screen_activity"] = "screen_activity"
    occurred_at: datetime
    payload: ScreenActivityPayload

class ScreenActivityPayload(BaseModel):
    # State change
    event_type: Literal["screen_on", "screen_off", "active_window_changed", "idle_started", "idle_ended"]
    
    # For active_window_changed
    window_title: str | None
    application_name: str | None
    application_bundle: str | None  # macOS bundle ID / Windows process name
    
    # For idle_started/ended
    idle_duration_seconds: int | None  # How long idle (for idle_ended)
    
    # Session info
    session_id: str                  # OS login session ID
    user_account: str | None         # Username (not email)
```

**When to emit:**
- Screen state change
- Active window change (throttled: max 1 per 5 seconds)
- Idle detection (30 seconds of no input)

**Privacy note:** Window titles can be sensitive. User can configure to only report application name, not window title.

---

### 9. Application Focus Event

> User switched to a new application.

```python
class ApplicationFocusEvent(BaseModel):
    type: Literal["application_focus"] = "application_focus"
    occurred_at: datetime
    payload: ApplicationFocusPayload

class ApplicationFocusPayload(BaseModel):
    application_name: str
    application_version: str | None
    window_title: str | None         # User can disable this
    
    # Focus metrics
    focus_duration_seconds: int      # How long user used this app
    
    # Category (auto-detected or user-configured)
    app_category: AppCategory

class AppCategory(str, Enum):
    BROWSER = "browser"
    CODE_EDITOR = "code_editor"
    TERMINAL = "terminal"
    COMMUNICATION = "communication"  # Slack, Teams, etc.
    PRODUCTIVITY = "productivity"     # Office apps
    MEDIA = "media"                   # Video, music
    DESIGN = "design"                # Figma, Photoshop
    MESSAGING = "messaging"          # WhatsApp, iMessage
    SOCIAL = "social"                 # Twitter, LinkedIn
    ENTERTAINMENT = "entertainment"   # Games, streaming
    OTHER = "other"
```

**When to emit:**
- Application focus change (throttled: max 1 per 10 seconds)
- Periodic summary (every 5 minutes if same app still focused)

---

### 10. Keyboard Activity Event

> Keystroke and mouse activity summary (aggregated, not raw).

```python
class KeyboardActivityEvent(BaseModel):
    type: Literal["keyboard_activity"] = "keyboard_activity"
    occurred_at: datetime
    payload: KeyboardActivityPayload

class KeyboardActivityPayload(BaseModel):
    # Time window
    window_start: datetime
    window_end: datetime
    duration_seconds: int
    
    # Aggregated activity (no raw keystrokes)
    keystrokes: int                  # Total keystrokes in window
    mouse_clicks: int                # Total mouse clicks
    mouse_scroll_events: int         # Scroll wheel events
    mouse_distance_px: int           # Total mouse movement (pixels)
    
    # Active/idle breakdown
    active_seconds: int              # Time with actual input
    idle_seconds: int
    
    # Typing pattern (optional)
    typing_speed_wpm: float | None   # Average words per minute
```

**When to emit:**
- Periodic summary (every 15 minutes)

**Privacy note:** Only aggregated counts, no raw keystrokes ever transmitted. This preserves privacy while still enabling productivity analysis.

---

## Common Events

### 11. Battery Event

> Battery level change.

```python
class BatteryEvent(BaseModel):
    type: Literal["battery"] = "battery"
    occurred_at: datetime
    payload: BatteryPayload

class BatteryPayload(BaseModel):
    level: float                     # 0.0 to 1.0
    is_charging: bool
    charging_type: Literal["usb", "ac", "wireless"] | None
    temperature: float | None        # Celsius
    health: Literal["good", "overheat", "dead", "over_voltage", "unspecified"] = "good"
```

**When to emit:**
- Level changed by > 5%
- Charging started/stopped
- Low battery warning (< 20%)

---

### 12. Network Status Event

> Network connectivity change.

```python
class NetworkStatusEvent(BaseModel):
    type: Literal["network_status"] = "network_status"
    occurred_at: datetime
    payload: NetworkStatusPayload

class NetworkStatusPayload(BaseModel):
    connected: bool
    network_type: NetworkType
    ssid: str | None                 # WiFi name (if applicable)
    signal_strength: int | None     # dBm
    ip_address: str | None           # Internal IP (optional)
    vpn_active: bool = False

class NetworkType(str, Enum):
    WIFI = "wifi"
    CELLULAR = "cellular"
    ETHERNET = "ethernet"
    BLUETOOTH = "bluetooth"
    NONE = "none"
```

**When to emit:**
- Network state change (connected/disconnected)
- Network type change (wifi ↔ cellular)
- Significant signal change (> 10 dBm)

---

## Event Union

```python
# All possible minion events
MinionEvent = Annotated[
    LocationEvent | ActivityEvent | CalendarEvent | AppUsageEvent | CallLogEvent |
    PaymentEvent | RefundEvent |
    ScreenActivityEvent | ApplicationFocusEvent | KeyboardActivityEvent |
    BatteryEvent | NetworkStatusEvent,
    Field(discriminator="type")
]
```

---

## Event Metadata (added by minion)

```python
class MinionEventMetadata(BaseModel):
    minion_id: UUID                  # Which minion sent this
    minion_type: MinionType          # "phone", "card", "laptop"
    sequence: int                    # Monotonic counter
    batch_id: UUID                  # Unique batch ID for this send
    device_time: datetime           # Device clock
    cortex_received_at: datetime    # Set by Cortex on receipt
    
class MinionEventBatch(BaseModel):
    metadata: MinionEventMetadata
    events: list[MinionEvent]
```

---

## Event Type Summary

| Event | Minion | Emitted When |
|-------|--------|--------------|
| `location` | Phone | GPS update (debounced) |
| `activity` | Phone | Activity transition / periodic |
| `calendar` | Phone | Event created/modified/deleted/reminder |
| `app_usage` | Phone | Periodic summary |
| `call_log` | Phone | Call ended |
| `payment` | Card | Transaction auth/settlement |
| `refund` | Card | Refund initiated/completed |
| `screen_activity` | Laptop | Screen state / window change / idle |
| `application_focus` | Laptop | App switch / periodic summary |
| `keyboard_activity` | Laptop | Periodic summary |
| `battery` | Phone/Laptop | Level change / charging |
| `network_status` | Phone/Laptop | Network change |

---

## Open Questions

- [ ] Call log — do we need `call_disposition` beyond `direction`?
- [ ] App usage — should we include `foreground_duration_seconds` separately from total duration?
- [ ] Location — should we include raw GPS data or only derived (speed, heading)?
- [ ] Keyboard — any other aggregated metrics useful? (e.g., special key percentage, copy/paste ratio)

---

## Status

✅ Schemas designed — ready for review

---

*Last updated: 2026-04-28*
