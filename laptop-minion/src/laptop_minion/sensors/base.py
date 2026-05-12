"""Base sensor interface and utilities."""

from __future__ import annotations

import abc
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable

from cortex_protocol.schemas import MinionEvent

if TYPE_CHECKING:
    from laptop_minion.config import SensorSettings

logger = logging.getLogger(__name__)


@dataclass
class SensorEvent:
    """Event emitted by a sensor."""

    sensor_name: str
    event: MinionEvent
    timestamp: datetime = None

    def __post_init__(self) -> None:
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()


class BaseSensor(abc.ABC):
    """Base class for all sensors.

    Sensors monitor system state and emit events when significant
    changes are detected. They handle their own polling/throttling
    via debounce intervals.

    Subclasses must implement:
    - _get_current_state() - get the current sensor reading
    - _create_event(state) - create a MinionEvent from the state
    - _should_emit(prev_state, new_state) - decide if an event should be emitted
    """

    def __init__(
        self,
        name: str,
        settings: SensorSettings,
        on_event: Callable[[SensorEvent], None],
    ):
        self.name = name
        self._settings = settings
        self._on_event = on_event
        self._running = False
        self._thread: threading.Thread | None = None
        self._last_event_time: datetime | None = None
        self._last_emit_time: float = 0
        self._lock = threading.Lock()
        self._current_state: dict[str, Any] = {}
        self._initialized = False

    @property
    def enabled(self) -> bool:
        """Whether the sensor is enabled."""
        return self._settings.enabled

    def start(self) -> None:
        """Start the sensor."""
        if not self.enabled:
            logger.info(f"Sensor {self.name} is disabled, skipping")
            return

        if self._running:
            logger.warning(f"Sensor {self.name} is already running")
            return

        logger.info(f"Starting sensor: {self.name}")
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"sensor-{self.name}")
        self._thread.start()

    def stop(self) -> None:
        """Stop the sensor."""
        if not self._running:
            return

        logger.info(f"Stopping sensor: {self.name}")
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        """Main sensor loop."""
        try:
            # Initialize state
            self._current_state = self._get_current_state()
            self._initialized = True

            # Emit initial state
            event = self._create_event(self._current_state)
            if event:
                self._emit(event)

            # Main polling loop
            while self._running:
                try:
                    self._poll()
                except Exception as e:
                    logger.error(f"Error in sensor {self.name}: {e}")

                # Sleep for sampling interval
                interval = self._settings.sampling_interval
                for _ in range(int(interval * 10)):
                    if not self._running:
                        break
                    time.sleep(0.1)

        except Exception as e:
            logger.error(f"Fatal error in sensor {self.name}: {e}")
        finally:
            self._running = False

    def _poll(self) -> None:
        """Poll for state changes."""
        prev_state = self._current_state
        new_state = self._get_current_state()
        self._current_state = new_state

        # Check if state changed in a meaningful way
        if self._should_emit(prev_state, new_state):
            # Check debounce
            if self._check_debounce():
                event = self._create_event(new_state)
                if event:
                    self._emit(event)

    def _check_debounce(self) -> bool:
        """Check if enough time has passed since last emit (debounce)."""
        debounce = self._settings.debounce_seconds
        if debounce is None:
            return True

        now = time.time()
        if now - self._last_emit_time >= debounce:
            self._last_emit_time = now
            return True
        return False

    def _emit(self, event: MinionEvent) -> None:
        """Emit an event."""
        self._last_event_time = datetime.utcnow()
        sensor_event = SensorEvent(
            sensor_name=self.name,
            event=event,
            timestamp=self._last_event_time,
        )
        logger.debug(f"Sensor {self.name} emitting event: {event.type}")
        self._on_event(sensor_event)

    @abc.abstractmethod
    def _get_current_state(self) -> dict[str, Any]:
        """Get the current sensor state.

        Returns a dict with the current readings.
        """
        ...

    @abc.abstractmethod
    def _create_event(self, state: dict[str, Any]) -> MinionEvent | None:
        """Create a MinionEvent from the current state.

        Returns None if no event should be emitted.
        """
        ...

    @abc.abstractmethod
    def _should_emit(self, prev_state: dict[str, Any], new_state: dict[str, Any]) -> bool:
        """Determine if an event should be emitted.

        Compare previous and new state to decide if a meaningful
        change occurred.
        """
        ...

    def get_status(self) -> dict[str, Any]:
        """Get sensor status for the status command."""
        return {
            "name": self.name,
            "enabled": self.enabled,
            "running": self._running,
            "initialized": self._initialized,
            "last_event": self._last_event_time.isoformat() if self._last_event_time else None,
        }
