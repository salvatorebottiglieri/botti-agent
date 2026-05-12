"""Battery sensor.

Monitors battery level changes >5%, charging state changes,
and low battery warnings.
"""

from __future__ import annotations

import logging
import platform
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cortex_protocol.schemas import BatteryEvent, BatteryPayload
from laptop_minion.config import SensorSettings
from laptop_minion.sensors.base import BaseSensor, SensorEvent

logger = logging.getLogger(__name__)


@dataclass
class BatteryState:
    """Battery state snapshot."""

    level: float  # 0.0 to 1.0
    is_charging: bool
    charging_type: str | None = None  # "usb", "ac", "wireless"
    temperature: float | None = None
    health: str = "good"


class BatterySensor(BaseSensor):
    """Battery sensor using psutil or platform-specific APIs.

    Triggers on:
    - Level changes > 5%
    - Charging started/stopped
    - Low battery warning (< 20%)
    - Critical battery warning (< 10%)
    """

    LOW_BATTERY_THRESHOLD = 0.20  # 20%
    CRITICAL_BATTERY_THRESHOLD = 0.10  # 10%
    LEVEL_CHANGE_THRESHOLD = 0.05  # 5%

    def __init__(
        self,
        settings: SensorSettings,
        on_event: SensorEvent,
    ):
        super().__init__("battery", settings, on_event)
        self._platform = platform.system()
        self._last_warned_level: float | None = None
        self._last_charging_state: bool | None = None

    def _get_current_state(self) -> dict[str, Any]:
        """Get current battery state based on platform."""
        if self._platform == "Darwin":
            return self._get_state_macos()
        elif self._platform == "Windows":
            return self._get_state_windows()
        else:
            return self._get_state_linux()

    def _get_state_macos(self) -> dict[str, Any]:
        """Get battery state on macOS using pmset."""
        state: dict[str, Any] = {
            "level": 1.0,
            "is_charging": False,
            "charging_type": None,
            "temperature": None,
            "health": "good",
        }

        try:
            import subprocess

            result = subprocess.run(
                ["pmset", "-g", "battery"],
                capture_output=True,
                text=True,
                timeout=2,
            )

            if result.returncode == 0:
                output = result.stdout

                # Parse current charge
                for line in output.split("\n"):
                    if "current capacity" in line.lower():
                        # "current capacity: 85%"
                        parts = line.split(":")
                        if len(parts) > 1:
                            percent_str = parts[1].strip().rstrip("%")
                            try:
                                state["level"] = float(percent_str) / 100.0
                            except ValueError:
                                pass

                    elif "charging" in line.lower():
                        state["is_charging"] = "yes" in line.lower() or "true" in line.lower()
                        if state["is_charging"]:
                            state["charging_type"] = "ac"

                    elif "InternalBattery" in line:
                        # Check for external power
                        if "AC" in line:
                            state["is_charging"] = True
                            state["charging_type"] = "ac"

        except subprocess.TimeoutExpired:
            logger.warning("pmset timed out")
        except Exception as e:
            logger.debug(f"macOS battery state error: {e}")

        # Fallback to psutil if available
        if state["level"] >= 1.0:
            try:
                import psutil

                battery = psutil.sensors_battery()
                if battery:
                    state["level"] = battery.percent / 100.0
                    state["is_charging"] = battery.power_plugged
                    if battery.power_plugged:
                        state["charging_type"] = "ac"
            except ImportError:
                pass

        return state

    def _get_state_windows(self) -> dict[str, Any]:
        """Get battery state on Windows using psutil or WMI."""
        state: dict[str, Any] = {
            "level": 1.0,
            "is_charging": False,
            "charging_type": None,
            "temperature": None,
            "health": "good",
        }

        try:
            import psutil

            battery = psutil.sensors_battery()
            if battery:
                state["level"] = battery.percent / 100.0
                state["is_charging"] = battery.power_plugged
                state["charging_type"] = "ac" if battery.power_plugged else None

        except ImportError:
            logger.warning("psutil not available for battery")
        except Exception as e:
            logger.debug(f"Windows battery state error: {e}")

        return state

    def _get_state_linux(self) -> dict[str, Any]:
        """Get battery state on Linux using /sys/class/power_supply."""
        state: dict[str, Any] = {
            "level": 1.0,
            "is_charging": False,
            "charging_type": None,
            "temperature": None,
            "health": "good",
        }

        # Try reading from /sys/class/power_supply
        try:
            import os

            battery_path = None
            for entry in os.listdir("/sys/class/power_supply"):
                if "BAT" in entry:
                    battery_path = f"/sys/class/power_supply/{entry}"
                    break

            if battery_path:
                # Read charge level
                charge_full = f"{battery_path}/charge_full"
                charge_now = f"{battery_path}/charge_now"

                if os.path.exists(charge_now):
                    with open(charge_now) as f:
                        now = int(f.read().strip())
                    with open(charge_full) as f:
                        full = int(f.read().strip())
                    if full > 0:
                        state["level"] = now / full

                # Read status
                status_file = f"{battery_path}/status"
                if os.path.exists(status_file):
                    with open(status_file) as f:
                        status = f.read().strip().lower()
                    state["is_charging"] = status == "charging"
                    if state["is_charging"]:
                        state["charging_type"] = "ac"

        except FileNotFoundError:
            # Fallback to psutil
            try:
                import psutil

                battery = psutil.sensors_battery()
                if battery:
                    state["level"] = battery.percent / 100.0
                    state["is_charging"] = battery.power_plugged
                    state["charging_type"] = "ac" if battery.power_plugged else None
            except ImportError:
                pass
        except Exception as e:
            logger.debug(f"Linux battery state error: {e}")

        return state

    def _create_event(self, state: dict[str, Any]) -> BatteryEvent | None:
        """Create a battery event."""
        payload = BatteryPayload(
            level=state.get("level", 0.0),
            is_charging=state.get("is_charging", False),
            charging_type=state.get("charging_type"),
            temperature=state.get("temperature"),
            health=self._map_health(state.get("health", "good")),
        )

        return BatteryEvent(
            occurred_at=datetime.utcnow(),
            payload=payload,
        )

    def _should_emit(self, prev_state: dict[str, Any], new_state: dict[str, Any]) -> bool:
        """Emit on significant battery changes."""
        prev_level = prev_state.get("level", 0)
        new_level = new_state.get("level", 0)

        # Level change > threshold
        if abs(new_level - prev_level) >= self.LEVEL_CHANGE_THRESHOLD:
            return True

        # Charging state change
        prev_charging = prev_state.get("is_charging")
        new_charging = new_state.get("is_charging")
        if prev_charging != new_charging:
            return True

        # Low battery warning
        if new_level <= self.LOW_BATTERY_THRESHOLD:
            if self._last_warned_level is None or self._last_warned_level > new_level:
                if new_level > self.CRITICAL_BATTERY_THRESHOLD:
                    # Only warn once per threshold crossing
                    if self._last_warned_level is None or self._last_warned_level > self.LOW_BATTERY_THRESHOLD:
                        self._last_warned_level = new_level
                        return True

        # Critical battery warning
        if new_level <= self.CRITICAL_BATTERY_THRESHOLD:
            if self._last_warned_level is None or self._last_warned_level > new_level:
                self._last_warned_level = new_level
                return True

        return False

    def _map_health(self, health: str) -> str:
        """Map battery health string to enum values."""
        health_lower = health.lower()
        if "good" in health_lower or "normal" in health_lower:
            return "good"
        elif "overheat" in health_lower:
            return "overheat"
        elif "dead" in health_lower:
            return "dead"
        elif "over_voltage" in health_lower:
            return "over_voltage"
        else:
            return "unspecified"

    def get_status(self) -> dict[str, Any]:
        """Get battery sensor status."""
        status = super().get_status()
        status["last_warned_level"] = self._last_warned_level
        status["low_threshold"] = self.LOW_BATTERY_THRESHOLD
        status["critical_threshold"] = self.CRITICAL_BATTERY_THRESHOLD
        return status
