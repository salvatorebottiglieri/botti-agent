"""Minion event Pydantic models.

All minion events follow a common structure:
- type: event discriminator
- occurred_at: when the event happened (device time)
- payload: event-specific data
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from cortex_protocol.schemas.enums import (
    ActivityType,
    AppCategory,
    MerchantCategory,
    NetworkType,
    TransactionType,
    UsageType,
)

# ─────────────────────────────────────────────────────────────────────────────
# Location Event (Phone)
# ─────────────────────────────────────────────────────────────────────────────


class LocationPayload(BaseModel):
    """Payload for location events."""

    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    altitude: float | None = None
    accuracy: float = Field(..., ge=0)
    altitude_accuracy: float | None = None
    speed: float | None = None
    heading: float | None = Field(default=None, ge=0, le=360)
    source: Literal["gps", "network", "fused"] = "gps"
    provider: str | None = None
    speed_category: Literal["stationary", "walking", "running", "cycling", "driving"] | None = None


class LocationEvent(BaseModel):
    """GPS coordinates from phone."""

    type: Literal["location"] = "location"
    occurred_at: datetime
    payload: LocationPayload


# ─────────────────────────────────────────────────────────────────────────────
# Activity Event (Phone)
# ─────────────────────────────────────────────────────────────────────────────


class ActivityPayload(BaseModel):
    """Payload for activity recognition events."""

    activity_type: ActivityType
    confidence: float = Field(..., ge=0, le=1)
    start_time: datetime
    duration_seconds: int = Field(..., ge=0)
    supporting_activities: list[ActivityType] = []
    supporting_confidences: list[float] = []


class ActivityEvent(BaseModel):
    """Detected physical activity."""

    type: Literal["activity"] = "activity"
    occurred_at: datetime
    payload: ActivityPayload


# ─────────────────────────────────────────────────────────────────────────────
# Calendar Event (Phone)
# ─────────────────────────────────────────────────────────────────────────────


class Attendee(BaseModel):
    """Calendar event attendee."""

    name: str | None = None
    email: str | None = None
    status: Literal["accepted", "declined", "tentative", "needs_action"] = "needs_action"


class CalendarPayload(BaseModel):
    """Payload for calendar events."""

    calendar_id: str
    event_id: str
    title: str
    start_time: datetime
    end_time: datetime
    all_day: bool = False
    timezone: str
    location: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    attendees: list[Attendee] = []
    organizer: str | None = None
    event_type: Literal["meeting", "reminder", "task", "out_of_office", "other"] = "other"
    status: Literal["confirmed", "tentative", "cancelled"] = "confirmed"
    recurrence: str | None = None


class CalendarEvent(BaseModel):
    """Upcoming calendar entry."""

    type: Literal["calendar"] = "calendar"
    occurred_at: datetime
    payload: CalendarPayload


# ─────────────────────────────────────────────────────────────────────────────
# App Usage Event (Phone)
# ─────────────────────────────────────────────────────────────────────────────


class AppUsagePayload(BaseModel):
    """Payload for app usage events."""

    window_start: datetime
    window_end: datetime
    duration_seconds: int = Field(..., ge=0)
    package_name: str
    app_name: str | None = None
    usage_type: UsageType
    foreground_duration_seconds: int = Field(..., ge=0)


class AppUsageEvent(BaseModel):
    """Application usage summary."""

    type: Literal["app_usage"] = "app_usage"
    occurred_at: datetime
    payload: AppUsagePayload


# ─────────────────────────────────────────────────────────────────────────────
# Call Log Event (Phone)
# ─────────────────────────────────────────────────────────────────────────────


class CallLogPayload(BaseModel):
    """Payload for call log events."""

    direction: Literal["incoming", "outgoing", "missed", "rejected", "blocked"]
    duration_seconds: int = Field(..., ge=0)
    contact_name: str | None = None
    phone_number: str | None = None  # Always null unless user opts in
    phone_number_hash: str | None = None
    answered: bool
    sim_slot: int = 0


class CallLogEvent(BaseModel):
    """Incoming/outgoing phone call."""

    type: Literal["call_log"] = "call_log"
    occurred_at: datetime
    payload: CallLogPayload


# ─────────────────────────────────────────────────────────────────────────────
# Payment Event (Card)
# ─────────────────────────────────────────────────────────────────────────────


class PaymentPayload(BaseModel):
    """Payload for payment events."""

    transaction_id: str
    amount: Decimal
    currency: str  # ISO 4217
    merchant_name: str
    merchant_category: MerchantCategory
    merchant_category_code: str  # ISO 18245 MCC code
    merchant_city: str | None = None
    merchant_country: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    card_last_four: str
    card_type: Literal["credit", "debit"] | None = None
    transaction_type: TransactionType
    status: Literal["completed", "pending", "declined", "refunded", "reversed"]
    refunded_amount: Decimal | None = None
    original_transaction_id: str | None = None


class PaymentEvent(BaseModel):
    """Card transaction."""

    type: Literal["payment"] = "payment"
    occurred_at: datetime
    payload: PaymentPayload


# ─────────────────────────────────────────────────────────────────────────────
# Refund Event (Card)
# ─────────────────────────────────────────────────────────────────────────────


class RefundPayload(BaseModel):
    """Payload for refund events."""

    refund_id: str
    original_transaction_id: str
    amount: Decimal
    currency: str
    merchant_name: str | None = None
    status: Literal["initiated", "completed", "failed"]
    initiated_at: datetime
    completed_at: datetime | None = None


class RefundEvent(BaseModel):
    """A refund to the card."""

    type: Literal["refund"] = "refund"
    occurred_at: datetime
    payload: RefundPayload


# ─────────────────────────────────────────────────────────────────────────────
# Screen Activity Event (Laptop)
# ─────────────────────────────────────────────────────────────────────────────


class ScreenActivityPayload(BaseModel):
    """Payload for screen activity events."""

    event_type: Literal[
        "screen_on", "screen_off", "active_window_changed", "idle_started", "idle_ended"
    ]
    window_title: str | None = None
    application_name: str | None = None
    application_bundle: str | None = None
    idle_duration_seconds: int | None = None
    session_id: str
    user_account: str | None = None


class ScreenActivityEvent(BaseModel):
    """Screen on/off and active window."""

    type: Literal["screen_activity"] = "screen_activity"
    occurred_at: datetime
    payload: ScreenActivityPayload


# ─────────────────────────────────────────────────────────────────────────────
# Application Focus Event (Laptop)
# ─────────────────────────────────────────────────────────────────────────────


class ApplicationFocusPayload(BaseModel):
    """Payload for application focus events."""

    application_name: str
    application_version: str | None = None
    window_title: str | None = None
    focus_duration_seconds: int = Field(..., ge=0)
    app_category: AppCategory


class ApplicationFocusEvent(BaseModel):
    """User switched to a new application."""

    type: Literal["application_focus"] = "application_focus"
    occurred_at: datetime
    payload: ApplicationFocusPayload


# ─────────────────────────────────────────────────────────────────────────────
# Keyboard Activity Event (Laptop)
# ─────────────────────────────────────────────────────────────────────────────


class KeyboardActivityPayload(BaseModel):
    """Payload for keyboard activity events."""

    window_start: datetime
    window_end: datetime
    duration_seconds: int = Field(..., ge=0)
    keystrokes: int = Field(..., ge=0)
    mouse_clicks: int = Field(..., ge=0)
    mouse_scroll_events: int = Field(..., ge=0)
    mouse_distance_px: int = Field(..., ge=0)
    active_seconds: int = Field(..., ge=0)
    idle_seconds: int = Field(..., ge=0)
    typing_speed_wpm: float | None = None


class KeyboardActivityEvent(BaseModel):
    """Keystroke and mouse activity summary (aggregated, not raw)."""

    type: Literal["keyboard_activity"] = "keyboard_activity"
    occurred_at: datetime
    payload: KeyboardActivityPayload


# ─────────────────────────────────────────────────────────────────────────────
# Battery Event (Common)
# ─────────────────────────────────────────────────────────────────────────────


class BatteryPayload(BaseModel):
    """Payload for battery events."""

    level: float = Field(..., ge=0, le=1)
    is_charging: bool
    charging_type: Literal["usb", "ac", "wireless"] | None = None
    temperature: float | None = None
    health: Literal["good", "overheat", "dead", "over_voltage", "unspecified"] = "good"


class BatteryEvent(BaseModel):
    """Battery level change."""

    type: Literal["battery"] = "battery"
    occurred_at: datetime
    payload: BatteryPayload


# ─────────────────────────────────────────────────────────────────────────────
# Network Status Event (Common)
# ─────────────────────────────────────────────────────────────────────────────


class NetworkStatusPayload(BaseModel):
    """Payload for network status events."""

    connected: bool
    network_type: NetworkType
    ssid: str | None = None
    signal_strength: int | None = None
    ip_address: str | None = None
    vpn_active: bool = False


class NetworkStatusEvent(BaseModel):
    """Network connectivity change."""

    type: Literal["network_status"] = "network_status"
    occurred_at: datetime
    payload: NetworkStatusPayload


# ─────────────────────────────────────────────────────────────────────────────
# Event Union
# ─────────────────────────────────────────────────────────────────────────────

MinionEvent = Annotated[
    LocationEvent
    | ActivityEvent
    | CalendarEvent
    | AppUsageEvent
    | CallLogEvent
    | PaymentEvent
    | RefundEvent
    | ScreenActivityEvent
    | ApplicationFocusEvent
    | KeyboardActivityEvent
    | BatteryEvent
    | NetworkStatusEvent,
    Field(discriminator="type"),
]
"""Union of all minion event types."""


__all__ = [
    "LocationEvent",
    "LocationPayload",
    "ActivityEvent",
    "ActivityPayload",
    "CalendarEvent",
    "CalendarPayload",
    "Attendee",
    "AppUsageEvent",
    "AppUsagePayload",
    "CallLogEvent",
    "CallLogPayload",
    "PaymentEvent",
    "PaymentPayload",
    "RefundEvent",
    "RefundPayload",
    "ScreenActivityEvent",
    "ScreenActivityPayload",
    "ApplicationFocusEvent",
    "ApplicationFocusPayload",
    "KeyboardActivityEvent",
    "KeyboardActivityPayload",
    "BatteryEvent",
    "BatteryPayload",
    "NetworkStatusEvent",
    "NetworkStatusPayload",
    "MinionEvent",
]
