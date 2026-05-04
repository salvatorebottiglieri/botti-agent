"""Enums for minion event types and categories."""

from __future__ import annotations

from enum import StrEnum


class ActivityType(StrEnum):
    """Physical activity detected by phone sensors."""

    IN_VEHICLE = "in_vehicle"  # Car, bus, train
    ON_BICYCLE = "on_bicycle"
    ON_FOOT = "on_foot"  # Walking or running
    RUNNING = "running"
    WALKING = "walking"
    STILL = "still"  # Not moving
    UNKNOWN = "unknown"


class UsageType(StrEnum):
    """Type of app usage detected."""

    FOREGROUND = "foreground"  # App in foreground
    BACKGROUND = "background"  # App running in background
    SYSTEM = "system"  # System interaction


class MerchantCategory(StrEnum):
    """Merchant category for payment transactions."""

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
    SERVICES = "services"  # Hairdresser, etc.
    UTILITIES = "utilities"
    OTHER = "other"


class TransactionType(StrEnum):
    """Type of card transaction."""

    PURCHASE = "purchase"
    REFUND = "refund"
    WITHDRAWAL = "withdrawal"
    FEE = "fee"
    TRANSFER = "transfer"


class AppCategory(StrEnum):
    """Category for laptop applications."""

    BROWSER = "browser"
    CODE_EDITOR = "code_editor"
    TERMINAL = "terminal"
    COMMUNICATION = "communication"  # Slack, Teams, etc.
    PRODUCTIVITY = "productivity"  # Office apps
    MEDIA = "media"  # Video, music
    DESIGN = "design"  # Fidget, Photoshop
    MESSAGING = "messaging"  # WhatsApp, iMessage
    SOCIAL = "social"  # Twitter, LinkedIn
    ENTERTAINMENT = "entertainment"  # Games, streaming
    OTHER = "other"


class NetworkType(StrEnum):
    """Type of network connection."""

    WIFI = "wifi"
    CELLULAR = "cellular"
    ETHERNET = "ethernet"
    BLUETOOTH = "bluetooth"
    NONE = "none"
