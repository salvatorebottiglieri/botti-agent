"""Screen activity and application focus sensors.

Monitors:
- Screen on/off state
- Active window changes
- Application focus switching
- Idle state
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from cortex_protocol.schemas import (
    AppCategory,
    ApplicationFocusEvent,
    ApplicationFocusPayload,
    ScreenActivityEvent,
    ScreenActivityPayload,
)
from laptop_minion.config import SensorSettings
from laptop_minion.sensors.base import BaseSensor, SensorEvent

logger = logging.getLogger(__name__)


@dataclass
class ScreenState:
    """Current screen state."""

    screen_on: bool = True
    window_title: str | None = None
    application_name: str | None = None
    application_bundle: str | None = None
    session_id: str = ""
    user_account: str | None = None
    idle_duration: int = 0  # seconds
    is_idle: bool = False


@dataclass
class FocusTracker:
    """Tracks application focus for periodic summaries."""

    window_start: datetime = field(default_factory=datetime.utcnow)
    current_app: str | None = None
    current_window: str | None = None
    focus_duration: int = 0  # seconds
    app_summary: dict[str, int] = field(default_factory=dict)  # app -> seconds


class ScreenSensor(BaseSensor):
    """Screen activity sensor using OS-specific hooks.

    macOS: Uses python-osascript for accessibility APIs
    Windows: Uses pywin32 for Win32 APIs
    Linux: Uses xdotool/xprop or /proc
    """

    def __init__(
        self,
        settings: SensorSettings,
        on_event: SensorEvent,
        session_id: str,
        user_account: str | None = None,
    ):
        super().__init__("screen_activity", settings, on_event)
        self._session_id = session_id
        self._user_account = user_account or os.environ.get("USER") or os.environ.get("USERNAME")
        self._focus_tracker = FocusTracker()
        self._last_window_check: float = 0
        self._platform = platform.system()

    def _get_current_state(self) -> dict[str, Any]:
        """Get current screen state based on platform."""
        if self._platform == "Darwin":
            return self._get_state_macos()
        elif self._platform == "Windows":
            return self._get_state_windows()
        else:
            return self._get_state_linux()

    def _get_state_macos(self) -> dict[str, Any]:
        """Get screen state on macOS using osascript."""
        state: dict[str, Any] = {
            "screen_on": True,
            "window_title": None,
            "application_name": None,
            "application_bundle": None,
        }

        try:
            # Get frontmost application
            script = '''
            tell application "System Events"
                set frontApp to first application process whose frontmost is true
                set appName to name of frontApp
                set bundleID to bundle identifier of frontApp
            end tell

            tell application "System Events"
                set windowName to name of window 1 of frontApp
            end tell

            return appName & "||" & bundleID & "||" & windowName
            '''

            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=2,
            )

            if result.returncode == 0 and result.stdout.strip():
                parts = result.stdout.strip().split("||")
                if len(parts) >= 2:
                    state["application_name"] = parts[0]
                    state["application_bundle"] = parts[1]
                    if len(parts) >= 3:
                        state["window_title"] = parts[2] if parts[2] else None

        except subprocess.TimeoutExpired:
            logger.warning("osascript timed out")
        except Exception as e:
            logger.debug(f"osascript error: {e}")

        # Check screen state via pmset
        try:
            result = subprocess.run(
                ["pmset", "-g", "powerstate", "IOPowerSources"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            # If we can query pmset, assume screen is on
            state["screen_on"] = True
        except Exception:
            state["screen_on"] = False

        return state

    def _get_state_windows(self) -> dict[str, Any]:
        """Get screen state on Windows using pywin32 or ctypes."""
        state: dict[str, Any] = {
            "screen_on": True,
            "window_title": None,
            "application_name": None,
            "application_bundle": None,
        }

        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32

            # Get foreground window
            hwnd = user32.GetForegroundWindow()
            if hwnd:
                # Get window title
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buffer, length + 1)
                    state["window_title"] = buffer.value

                # Get process name
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                kernel32 = ctypes.windll.kernel32
                kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                kernel32.OpenProcess.restype = wintypes.HANDLE
                process_handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, hwnd)

                if process_handle:
                    buffer_size = 260
                    buffer = ctypes.create_unicode_buffer(buffer_size)
                    size = ctypes.wintypes.DWORD(buffer_size)

                    kernel32.QueryFullProcessImageNameW.argtypes = [
                        wintypes.HANDLE,
                        wintypes.DWORD,
                        wintypes.LPWSTR,
                        ctypes.POINTER(wintypes.DWORD),
                    ]

                    if kernel32.QueryFullProcessImageNameW(process_handle, 0, buffer, ctypes.byref(size)):
                        # Extract exe name from full path
                        exe_name = buffer.value.split("\\")[-1] if buffer.value else None
                        state["application_name"] = exe_name

                    kernel32.CloseHandle(process_handle)

        except ImportError:
            logger.warning("pywin32 not available, screen sensor limited")
        except Exception as e:
            logger.debug(f"Windows screen state error: {e}")

        return state

    def _get_state_linux(self) -> dict[str, Any]:
        """Get screen state on Linux using xdotool or /proc."""
        state: dict[str, Any] = {
            "screen_on": True,
            "window_title": None,
            "application_name": None,
            "application_bundle": None,
        }

        try:
            # Get active window using xdotool
            result = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowname"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                state["window_title"] = result.stdout.strip()

            # Get window class
            result = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowclassname"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                state["application_name"] = result.stdout.strip()

        except FileNotFoundError:
            logger.debug("xdotool not available on Linux")
        except Exception as e:
            logger.debug(f"Linux screen state error: {e}")

        return state

    def _create_event(self, state: dict[str, Any]) -> ScreenActivityEvent | None:
        """Create a screen activity event."""
        prev = self._current_state

        # Determine event type
        event_type = "active_window_changed"
        idle_duration = None

        # Check for screen on/off
        if state.get("screen_on") != prev.get("screen_on"):
            event_type = "screen_on" if state.get("screen_on") else "screen_off"

        # Check for idle transition
        elif state.get("is_idle") and not prev.get("is_idle"):
            event_type = "idle_started"
            idle_duration = state.get("idle_duration", 0)

        elif not state.get("is_idle") and prev.get("is_idle"):
            event_type = "idle_ended"

        # Only emit on actual changes
        if (
            event_type == "active_window_changed"
            and state.get("window_title") == prev.get("window_title")
            and state.get("application_name") == prev.get("application_name")
        ):
            return None

        payload = ScreenActivityPayload(
            event_type=event_type,
            window_title=state.get("window_title"),
            application_name=state.get("application_name"),
            application_bundle=state.get("application_bundle"),
            idle_duration_seconds=idle_duration,
            session_id=self._session_id,
            user_account=self._user_account,
        )

        return ScreenActivityEvent(
            occurred_at=datetime.utcnow(),
            payload=payload,
        )

    def _should_emit(self, prev_state: dict[str, Any], new_state: dict[str, Any]) -> bool:
        """Emit on any meaningful screen state change."""
        # Screen state change
        if new_state.get("screen_on") != prev_state.get("screen_on"):
            return True

        # Window/application change
        if (
            new_state.get("window_title") != prev_state.get("window_title")
            or new_state.get("application_name") != prev_state.get("application_name")
        ):
            return True

        # Idle state change
        if new_state.get("is_idle") != prev_state.get("is_idle"):
            return True

        return False


class ApplicationFocusSensor(BaseSensor):
    """Application focus sensor that tracks app switching.

    Provides:
    - App switch events (immediate)
    - 5-minute periodic summary of app usage
    """

    # Categories mapping for common apps
    APP_CATEGORIES: dict[str, AppCategory] = {
        "Safari": AppCategory.BROWSER,
        "Chrome": AppCategory.BROWSER,
        "Firefox": AppCategory.BROWSER,
        "Microsoft Edge": AppCategory.BROWSER,
        "Code": AppCategory.CODE_EDITOR,
        "Visual Studio Code": AppCategory.CODE_EDITOR,
        "Terminal": AppCategory.TERMINAL,
        "iTerm2": AppCategory.TERMINAL,
        "Slack": AppCategory.COMMUNICATION,
        "Discord": AppCategory.COMMUNICATION,
        "Teams": AppCategory.COMMUNICATION,
        "Mail": AppCategory.OTHER,
        "Outlook": AppCategory.OTHER,
        "Finder": AppCategory.OTHER,
        "Explorer": AppCategory.OTHER,
        "Notes": AppCategory.OTHER,
        "Notion": AppCategory.PRODUCTIVITY,
        "Obsidian": AppCategory.PRODUCTIVITY,
    }

    def __init__(
        self,
        settings: SensorSettings,
        on_event: SensorEvent,
        session_id: str,
    ):
        super().__init__("application_focus", settings, on_event)
        self._session_id = session_id
        self._current_app: str | None = None
        self._focus_start: datetime = datetime.utcnow()
        self._app_durations: dict[str, int] = {}
        self._last_summary_time = datetime.utcnow()
        self._summary_interval = 300  # 5 minutes

    def _get_current_state(self) -> dict[str, Any]:
        """Get current focus state."""
        # Use ScreenSensor's state gathering if available
        # For now, use basic platform detection
        import platform

        state: dict[str, Any] = {"application_name": None, "window_title": None}

        if platform.system() == "Darwin":
            try:
                script = '''
                tell application "System Events"
                    set frontApp to first application process whose frontmost is true
                    set appName to name of frontApp
                end tell
                return appName
                '''
                result = subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if result.returncode == 0:
                    state["application_name"] = result.stdout.strip()
            except Exception:
                pass

        elif platform.system() == "Windows":
            try:
                import ctypes
                from ctypes import wintypes

                user32 = ctypes.windll.user32
                hwnd = user32.GetForegroundWindow()
                if hwnd:
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buffer = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buffer, length + 1)
                        state["window_title"] = buffer.value
            except Exception:
                pass

        return state

    def _create_event(self, state: dict[str, Any]) -> ApplicationFocusEvent | None:
        """Create an application focus event."""
        app_name = state.get("application_name")
        if not app_name:
            return None

        now = datetime.utcnow()
        duration = int((now - self._focus_start).total_seconds())

        payload = ApplicationFocusPayload(
            application_name=app_name,
            window_title=state.get("window_title"),
            focus_duration_seconds=duration,
            app_category=self._get_category(app_name),
        )

        return ApplicationFocusEvent(
            occurred_at=now,
            payload=payload,
        )

    def _should_emit(self, prev_state: dict[str, Any], new_state: dict[str, Any]) -> bool:
        """Emit when app changes or on periodic summary."""
        new_app = new_state.get("application_name")

        # App changed
        if new_app and new_app != self._current_app:
            # Update duration tracking
            if self._current_app:
                now = datetime.utcnow()
                duration = int((now - self._focus_start).total_seconds())
                self._app_durations[self._current_app] = (
                    self._app_durations.get(self._current_app, 0) + duration
                )
                self._focus_tracker.focus_duration += duration
                self._focus_tracker.app_summary[self._current_app] = (
                    self._focus_tracker.app_summary.get(self._current_app, 0) + duration
                )

            self._current_app = new_app
            self._focus_start = datetime.utcnow()
            return True

        # Periodic summary
        now = datetime.utcnow()
        if (now - self._last_summary_time).total_seconds() >= self._summary_interval:
            self._last_summary_time = now
            # Reset tracking for next period
            self._app_durations.clear()
            return True

        return False

    def _get_category(self, app_name: str) -> AppCategory:
        """Get the category for an application."""
        return self.APP_CATEGORIES.get(app_name, AppCategory.OTHER)
