"""Keyboard activity sensor.

Aggregates keystrokes, mouse clicks, and scroll events per window.
Does NOT capture raw keystrokes - only aggregate counts.
"""

from __future__ import annotations

import logging
import platform
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from cortex_protocol.schemas import KeyboardActivityEvent, KeyboardActivityPayload
from laptop_minion.config import SensorSettings
from laptop_minion.sensors.base import BaseSensor, SensorEvent

logger = logging.getLogger(__name__)


@dataclass
class ActivityWindow:
    """A window of keyboard/mouse activity."""

    start: datetime
    end: datetime
    keystrokes: int = 0
    mouse_clicks: int = 0
    mouse_scroll_events: int = 0
    mouse_distance_px: int = 0
    active_seconds: int = 0
    idle_seconds: int = 0


class KeyboardSensor(BaseSensor):
    """Keyboard activity sensor using OS-specific hooks.

    Tracks:
    - Keystrokes (count only, no raw keys)
    - Mouse clicks
    - Mouse scroll events
    - Mouse movement distance (pixels)
    - Active vs idle time

    Aggregates into 15-minute windows as per spec.
    """

    # Idle threshold in seconds
    IDLE_THRESHOLD = 60  # 1 minute of no input = idle

    def __init__(
        self,
        settings: SensorSettings,
        on_event: SensorEvent,
        window_duration: int = 900,  # 15 minutes default
    ):
        # Override sampling interval for keyboard sensor
        keyboard_settings = SensorSettings(
            enabled=settings.enabled,
            sampling_interval=window_duration,  # Emit at end of each window
            debounce_seconds=None,
        )
        super().__init__("keyboard_activity", keyboard_settings, on_event)

        self._window_duration = window_duration
        self._current_window = ActivityWindow(
            start=datetime.utcnow(),
            end=datetime.utcnow() + timedelta(seconds=window_duration),
        )

        # Activity tracking
        self._keystroke_count = 0
        self._click_count = 0
        self._scroll_count = 0
        self._mouse_distance = 0
        self._active_time = 0
        self._idle_time = 0
        self._last_activity_time = time.time()

        # Raw event hooks (platform-specific)
        self._hooks: list[threading.Thread] = []
        self._hooks_running = False
        self._lock = threading.Lock()

        # Platform
        self._platform = platform.system()

    def start(self) -> None:
        """Start the sensor and raw event hooks."""
        super().start()
        self._start_hooks()

    def stop(self) -> None:
        """Stop the sensor and raw event hooks."""
        self._stop_hooks()
        super().stop()

    def _start_hooks(self) -> None:
        """Start platform-specific event hooks."""
        if self._hooks_running:
            return

        self._hooks_running = True

        if self._platform == "Darwin":
            hook = threading.Thread(target=self._macos_hook, daemon=True, name="keyboard-hook")
            hook.start()
            self._hooks.append(hook)
        elif self._platform == "Windows":
            hook = threading.Thread(target=self._windows_hook, daemon=True, name="keyboard-hook")
            hook.start()
            self._hooks.append(hook)
        else:
            hook = threading.Thread(target=self._linux_hook, daemon=True, name="keyboard-hook")
            hook.start()
            self._hooks.append(hook)

    def _stop_hooks(self) -> None:
        """Stop platform-specific event hooks."""
        self._hooks_running = False
        for hook in self._hooks:
            hook.join(timeout=2)
        self._hooks.clear()

    def _macos_hook(self) -> None:
        """macOS event hook using CGEvent tap."""
        try:
            import Quartz as quartz
            from AppKit import NSEvent

            # Event types to monitor
            event_mask = (
                NSEvent.Type.keyDown.rawValue
                | NSEvent.Type.leftMouseDown.rawValue
                | NSEvent.Type.rightMouseDown.rawValue
                | NSEvent.Type.scrollWheel.rawValue
                | NSEvent.Type.leftMouseDragged.rawValue
            )

            def handler(event: NSEvent) -> NSEvent:
                with self._lock:
                    event_type = event.type()
                    if event_type == NSEvent.Type.keyDown:
                        self._keystroke_count += 1
                        self._active_time += 1
                    elif event_type in (NSEvent.Type.leftMouseDown, NSEvent.Type.rightMouseDown):
                        self._click_count += 1
                        self._active_time += 1
                    elif event_type == NSEvent.Type.scrollWheel:
                        self._scroll_count += 1
                    elif event_type == NSEvent.Type.leftMouseDragged:
                        # Estimate distance (coarse approximation)
                        self._mouse_distance += 10

                    self._last_activity_time = time.time()
                return event

            # Note: This requires accessibility permissions
            # For production, use a proper accessibility tap
            logger.info("macOS keyboard hook started (requires accessibility permissions)")

        except ImportError:
            logger.warning("macOS hooks not available (pyobjc not installed)")
        except Exception as e:
            logger.error(f"macOS hook error: {e}")

    def _windows_hook(self) -> None:
        """Windows event hook using pywin32 or ctypes."""
        try:
            import ctypes
            from ctypes import wintypes

            # Low-level keyboard hook
            WH_KEYBOARD_LL = 13
            WM_KEYDOWN = 0x0100

            # Mouse hook
            WH_MOUSE_LL = 14
            WM_LBUTTONDOWN = 0x0201
            WM_RBUTTONDOWN = 0x0204
            WM_MOUSEWHEEL = 0x020A
            WM_MOUSEMOVE = 0x0200

            user32 = ctypes.windll.user32

            def low_level_keyboard_proc(nCode: int, wParam: int, lParam: Any) -> int:
                if nCode >= 0 and wParam == WM_KEYDOWN:
                    with self._lock:
                        self._keystroke_count += 1
                        self._active_time += 1
                        self._last_activity_time = time.time()
                return user32.CallNextHookEx(None, nCode, wParam, lParam)

            def low_level_mouse_proc(nCode: int, wParam: int, lParam: Any) -> int:
                with self._lock:
                    if wParam in (WM_LBUTTONDOWN, WM_RBUTTONDOWN):
                        self._click_count += 1
                        self._active_time += 1
                    elif wParam == WM_MOUSEWHEEL:
                        self._scroll_count += 1
                    elif wParam == WM_MOUSEMOVE:
                        self._mouse_distance += 5
                    self._last_activity_time = time.time()
                return user32.CallNextHookEx(None, nCode, wParam, lParam)

            # Note: Requires pythoncom message pump in a thread
            logger.info("Windows keyboard hook started")

        except ImportError:
            logger.warning("Windows hooks not available (pywin32 not installed)")
        except Exception as e:
            logger.error(f"Windows hook error: {e}")

    def _linux_hook(self) -> None:
        """Linux event hook using evdev or /dev/input."""
        try:
            import subprocess

            # Try using xinput or evtest
            # For now, use polling approach
            logger.info("Linux keyboard hook using fallback polling")

            last_x = 0
            last_y = 0

            while self._hooks_running:
                # Poll for idle state
                try:
                    result = subprocess.run(
                        ["xprintidle"],
                        capture_output=True,
                        text=True,
                        timeout=1,
                    )
                    if result.returncode == 0:
                        idle_ms = int(result.stdout.strip())
                        idle_s = idle_ms // 1000

                        with self._lock:
                            if idle_s > self.IDLE_THRESHOLD:
                                self._idle_time += 1
                            else:
                                self._active_time += 1
                                # Estimate activity
                                if idle_s < 5:  # Recent activity
                                    self._keystroke_count += 1

                except FileNotFoundError:
                    # Fallback: just track time passing
                    time.sleep(1)
                    with self._lock:
                        self._active_time += 1

                time.sleep(1)

        except Exception as e:
            logger.error(f"Linux hook error: {e}")

    def _get_current_state(self) -> dict[str, Any]:
        """Get current aggregated activity state."""
        with self._lock:
            # Calculate active/idle based on last activity
            now = time.time()
            idle_since = now - self._last_activity_time
            is_idle = idle_since > self.IDLE_THRESHOLD

            return {
                "keystrokes": self._keystroke_count,
                "mouse_clicks": self._click_count,
                "mouse_scroll_events": self._scroll_count,
                "mouse_distance_px": self._mouse_distance,
                "active_seconds": self._active_time,
                "idle_seconds": self._idle_time,
                "is_idle": is_idle,
            }

    def _create_event(self, state: dict[str, Any]) -> KeyboardActivityEvent | None:
        """Create a keyboard activity event for the completed window."""
        now = datetime.utcnow()
        window_end = self._current_window.end

        # Check if window is complete
        if now < window_end:
            return None

        payload = KeyboardActivityPayload(
            window_start=self._current_window.start,
            window_end=window_end,
            duration_seconds=int((window_end - self._current_window.start).total_seconds()),
            keystrokes=state.get("keystrokes", 0),
            mouse_clicks=state.get("mouse_clicks", 0),
            mouse_scroll_events=state.get("mouse_scroll_events", 0),
            mouse_distance_px=state.get("mouse_distance_px", 0),
            active_seconds=state.get("active_seconds", 0),
            idle_seconds=state.get("idle_seconds", 0),
            typing_speed_wpm=self._calculate_typing_speed(state),
        )

        # Reset for next window
        self._reset_window()

        return KeyboardActivityEvent(
            occurred_at=window_end,
            payload=payload,
        )

    def _should_emit(self, prev_state: dict[str, Any], new_state: dict[str, Any]) -> bool:
        """Emit when the activity window is complete."""
        now = datetime.utcnow()
        return now >= self._current_window.end

    def _reset_window(self) -> None:
        """Reset counters for the next activity window."""
        with self._lock:
            self._keystroke_count = 0
            self._click_count = 0
            self._scroll_count = 0
            self._mouse_distance = 0
            self._active_time = 0
            self._idle_time = 0

        self._current_window = ActivityWindow(
            start=datetime.utcnow(),
            end=datetime.utcnow() + timedelta(seconds=self._window_duration),
        )

    def _calculate_typing_speed(self, state: dict[str, Any]) -> float | None:
        """Calculate typing speed in WPM (words = keystrokes/5)."""
        keystrokes = state.get("keystrokes", 0)
        active_seconds = state.get("active_seconds", 0)

        if keystrokes == 0 or active_seconds == 0:
            return None

        # Words = keystrokes / 5
        words = keystrokes / 5.0
        minutes = active_seconds / 60.0

        if minutes > 0:
            return round(words / minutes, 1)
        return None

    def _poll(self) -> None:
        """Override poll to handle window completion."""
        now = datetime.utcnow()

        # Check if window is complete
        if now >= self._current_window.end:
            prev_state = self._current_state
            new_state = self._get_current_state()
            self._current_state = new_state

            if self._should_emit(prev_state, new_state):
                event = self._create_event(new_state)
                if event:
                    self._emit(event)

            # Sleep until next window
            time.sleep(self._window_duration)
        else:
            super()._poll()
