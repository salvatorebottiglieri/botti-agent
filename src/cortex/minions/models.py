"""Minion-related data models."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, NotRequired, TypedDict


class SensorType(StrEnum):
    """Types of sensors available on minions."""

    LOCATION = "location"
    CALENDAR = "calendar"
    ACTIVITY = "activity"
    APP_USAGE = "app_usage"
    BATTERY = "battery"
    NOTIFICATIONS = "notifications"
    CUSTOM = "custom"


class MinionState(StrEnum):
    """Minion connection states."""

    CONNECTING = "connecting"
    ONLINE = "online"
    AWAY = "away"
    OFFLINE = "offline"


class EventType(StrEnum):
    """Minion event types."""

    # Core lifecycle
    REGISTERED = "minion.registered"
    HEARTBEAT = "minion.heartbeat"
    DISCONNECTED = "minion.disconnected"

    # Sensor events
    LOCATION_UPDATE = "location.update"
    CALENDAR_EVENT = "calendar.event"
    ACTIVITY_DETECTED = "activity.detected"
    APP_USAGE = "app.usage"
    PAYMENT = "payment"
    CALL_LOG = "call_log"
    BATTERY_LEVEL = "battery.level"
    NOTIFICATION = "notification"

    # Custom events
    CUSTOM_EVENT = "custom.event"


# ─────────────────────────────────────────────────────────────────────────────
# Request/Response Payloads (TypedDicts for schema validation)
# ─────────────────────────────────────────────────────────────────────────────


class MinionCapabilities(TypedDict, total=False):
    """Sensors and features a minion supports."""

    sensors: list[SensorType]
    max_batch_size: int
    supports_compression: bool
    battery_monitored: bool


class LocationPayload(TypedDict):
    """Payload for location update events."""

    latitude: float
    longitude: float
    accuracy: float
    altitude: NotRequired[float]
    speed: NotRequired[float]
    heading: NotRequired[float]
    timestamp: str


class CalendarPayload(TypedDict):
    """Payload for calendar events."""

    event_id: str
    title: str
    start_time: str
    end_time: NotRequired[str]
    location: NotRequired[str]
    attendees: NotRequired[list[str]]


class ActivityPayload(TypedDict):
    """Payload for activity detection."""

    activity: str  # walking, running, driving, etc.
    confidence: float
    start_time: str
    duration_seconds: NotRequired[int]


class AppUsagePayload(TypedDict):
    """Payload for app usage events."""

    app_id: str
    app_name: str
    duration_seconds: int
    timestamp: str


class BatteryPayload(TypedDict):
    """Payload for battery level events."""

    level: float  # 0.0 to 1.0
    is_charging: bool
    timestamp: str


class CustomPayload(TypedDict):
    """Payload for custom events."""

    event_name: str
    data: dict[str, Any]
    timestamp: str


# ─────────────────────────────────────────────────────────────────────────────
# Dataclasses (for internal use)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class MinionInfo:
    """Information about a registered minion."""

    minion_id: str
    name: str
    device_type: str
    capabilities: MinionCapabilities
    state: MinionState = MinionState.OFFLINE
    last_heartbeat: datetime | None = None
    last_location: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "minion_id": self.minion_id,
            "name": self.name,
            "device_type": self.device_type,
            "capabilities": self.capabilities,
            "state": self.state.value,
            "last_heartbeat": self.last_heartbeat.isoformat() if self.last_heartbeat else None,
            "last_location": self.last_location,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MinionInfo:
        """Create from dictionary."""
        return cls(
            minion_id=data["minion_id"],
            name=data["name"],
            device_type=data["device_type"],
            capabilities=data.get("capabilities", {}),
            state=MinionState(data.get("state", "offline")),
            last_heartbeat=datetime.fromisoformat(data["last_heartbeat"]) if data.get("last_heartbeat") else None,
            last_location=data.get("last_location"),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(UTC),
            metadata=data.get("metadata", {}),
        )


@dataclass
class MinionEvent:
    """A single event from a minion."""

    event_id: str
    minion_id: str
    event_type: EventType
    payload: dict[str, Any]
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    sequence_number: int = 0

    @classmethod
    def create(
        cls,
        minion_id: str,
        event_type: EventType | str,
        payload: dict[str, Any],
    ) -> MinionEvent:
        """Create a new event with generated ID."""
        return cls(
            event_id=str(uuid.uuid4()),
            minion_id=minion_id,
            event_type=EventType(event_type) if isinstance(event_type, str) else event_type,
            payload=payload,
            timestamp=datetime.now(UTC),
            sequence_number=0,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "event_id": self.event_id,
            "minion_id": self.minion_id,
            "event_type": self.event_type.value,
            "payload": self.payload,
            "timestamp": self.timestamp.isoformat(),
            "sequence_number": self.sequence_number,
        }


@dataclass
class MinionEventBatch:
    """A batch of events from a minion."""

    batch_id: str
    minion_id: str
    events: list[MinionEvent]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    compressed: bool = False

    @classmethod
    def create(cls, minion_id: str, events: list[MinionEvent]) -> MinionEventBatch:
        """Create a new batch."""
        return cls(
            batch_id=str(uuid.uuid4()),
            minion_id=minion_id,
            events=events,
            created_at=datetime.now(UTC),
            compressed=False,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "batch_id": self.batch_id,
            "minion_id": self.minion_id,
            "events": [e.to_dict() for e in self.events],
            "created_at": self.created_at.isoformat(),
            "compressed": self.compressed,
        }


@dataclass
class MinionConfig:
    """Configuration for minion client connection."""

    broker_url: str
    minion_id: str
    minion_name: str
    device_type: str = "unknown"
    port: int = 1883
    username: str | None = None
    password: str | None = None
    keepalive: int = 60
    qos: int = 1
    topics: list[str] | None = None

    def __post_init__(self) -> None:
        """Set default topics if not provided."""
        if self.topics is None:
            self.topics = [
                f"minions/{self.minion_id}/events",
                f"minions/{self.minion_id}/heartbeat",
            ]
