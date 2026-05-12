"""Main CLI entry point for laptop-minion.

Usage:
    laptop-minion run --broker mqtt://... --token ...
    laptop-minion status
    laptop-minion init
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

from laptop_minion import __version__
from laptop_minion.config import (
    Config,
    create_default_config,
    get_config_dir,
    get_config_path,
    get_state_path,
    load_config,
    save_config,
)
from laptop_minion.mqtt_client import CortexMQTTClient
from laptop_minion.sensors import (
    ApplicationFocusSensor,
    BatterySensor,
    KeyboardSensor,
    NetworkSensor,
    ScreenSensor,
    SensorEvent,
)
from laptop_minion.queue import OfflineQueue

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def cli() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="laptop-minion",
        description="Cortex laptop minion - sensor data collector",
    )
    parser.add_argument("--version", action="version", version=f"laptop-minion {__version__}")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # run command
    run_parser = subparsers.add_parser("run", help="Run the minion")
    run_parser.add_argument(
        "--broker",
        "-b",
        dest="broker_url",
        help="MQTT broker URL (e.g., mqtt://192.168.1.100:1883)",
    )
    run_parser.add_argument("--token", "-t", help="Minion authentication token")
    run_parser.add_argument(
        "--minion-id",
        help="Minion ID (auto-generated if not provided)",
    )
    run_parser.add_argument(
        "--config",
        "-c",
        type=Path,
        help="Path to config file",
    )

    # status command
    status_parser = subparsers.add_parser("status", help="Show minion status")

    # init command
    init_parser = subparsers.add_parser("init", help="Initialize configuration")
    init_parser.add_argument(
        "--broker",
        "-b",
        dest="broker_url",
        help="MQTT broker URL",
    )
    init_parser.add_argument("--token", "-t", help="Minion authentication token")
    init_parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Overwrite existing config",
    )

    args = parser.parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Route to command
    if args.command == "run":
        run(args)
    elif args.command == "status":
        status(args)
    elif args.command == "init":
        init(args)


def run(args: argparse.Namespace) -> None:
    """Run the minion."""
    # Load config
    try:
        config = load_config(
            broker_url=args.broker_url,
            token=args.token,
            minion_id=args.minion_id,
            config_path=args.config,
        )
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        sys.exit(1)

    # Validate config
    if not config.broker_url:
        logger.error("Broker URL is required (--broker or broker_url in config)")
        sys.exit(1)

    if not config.token:
        logger.error("Token is required (--token or token in config)")
        sys.exit(1)

    logger.info(f"Starting laptop-minion (ID: {config.minion_id})")
    logger.info(f"Broker: {config.broker_url}")

    # Create and run the minion
    minion = LaptopMinion(config)
    minion.start()

    # Wait for interrupt
    try:
        while minion.running:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    finally:
        minion.stop()
        logger.info("Minion stopped")


def status(args: argparse.Namespace) -> None:
    """Show minion status."""
    config_dir = get_config_dir()

    if not config_dir.exists():
        print("Minion not configured. Run 'laptop-minion init' first.")
        sys.exit(1)

    state_path = get_state_path()
    if not state_path.exists():
        print("Minion not initialized. Run 'laptop-minion init' first.")
        sys.exit(1)

    # Load state
    import yaml

    with open(state_path) as f:
        state = yaml.safe_load(f) or {}

    minion_id = state.get("minion_id", "unknown")

    # Check queue
    queue = OfflineQueue()
    queue_stats = queue.stats

    # Try to connect briefly for connection status
    try:
        config = load_config()
        client = CortexMQTTClient(config)
        client.connect(timeout=5)
        time.sleep(1)
        status = client.status
        client.disconnect()

        print(f"Minion ID: {minion_id}")
        print(f"Minion Type: laptop")
        print(f"Config: {config_dir}")
        print()
        print("Connection Status:")
        print(f"  Connected: {status.connected}")
        print(f"  Connecting: {status.connecting}")
        print(f"  Last Connected: {status.last_connected_at}")
        print(f"  Reconnect Attempts: {status.reconnect_attempts}")
        print()
        print("Event Statistics:")
        print(f"  Sent: {client.stats.sent}")
        print(f"  Queued: {client.stats.queued}")
        print(f"  Flushed: {client.stats.flushed}")
        print(f"  Failed: {client.stats.failed}")
        print()
        print("Offline Queue:")
        print(f"  Pending Events: {queue_stats['pending_events']}")
        print(f"  Flushed Events: {queue_stats['flushed_events']}")
        print(f"  Total Events: {queue_stats['total_events']}")
        print(f"  Queue Size: {queue_stats['queue_size_bytes'] / 1024:.1f} KB")

    except Exception as e:
        print(f"Minion ID: {minion_id}")
        print(f"Error connecting to broker: {e}")
        print()
        print("Offline Queue:")
        print(f"  Pending Events: {queue_stats['pending_events']}")
        print(f"  Flushed Events: {queue_stats['flushed_events']}")


def init(args: argparse.Namespace) -> None:
    """Initialize configuration."""
    config_path = get_config_path()

    if config_path.exists() and not args.force:
        print(f"Config already exists at {config_path}")
        print("Use --force to overwrite")
        sys.exit(1)

    # Create config
    config = Config()

    # Prompt for required values if not provided
    if args.broker_url:
        config.broker_url = args.broker_url
    else:
        config.broker_url = prompt("MQTT Broker URL", "mqtt://localhost:1883")

    if args.token:
        config.token = args.token
    else:
        config.token = prompt("Minion Token", "")

    # Save config
    save_config(config)

    # Generate minion_id
    minion_id = load_config().minion_id

    print()
    print(f"Configuration saved to {config_path}")
    print(f"Minion ID: {minion_id}")
    print()
    print("Next steps:")
    print("1. Register this minion in Cortex admin")
    print(f"2. Run: laptop-minion run --broker {config.broker_url} --token {config.token[:8]}...")


def prompt(message: str, default: str) -> str:
    """Prompt for input."""
    try:
        value = input(f"{message} [{default}]: ").strip()
        return value or default
    except (EOFError, KeyboardInterrupt):
        return default


# ─────────────────────────────────────────────────────────────────────────────
# Laptop Minion
# ─────────────────────────────────────────────────────────────────────────────


class LaptopMinion:
    """Main minion orchestrator.

    Manages sensors, MQTT connection, and event batching.
    """

    def __init__(self, config: Config):
        self._config = config
        self._sensors: list[ScreenSensor | ApplicationFocusSensor | KeyboardSensor | BatterySensor | NetworkSensor] = []
        self._mqtt: CortexMQTTClient | None = None
        self._running = False
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        """Check if minion is running."""
        with self._lock:
            return self._running

    def start(self) -> None:
        """Start the minion."""
        with self._lock:
            if self._running:
                return
            self._running = True

        # Setup signal handlers
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        # Create MQTT client
        self._mqtt = CortexMQTTClient(
            config=self._config,
            on_connect=self._on_connect,
            on_disconnect=self._on_disconnect,
        )

        # Connect
        if not self._mqtt.connect():
            logger.warning("Failed to connect, will retry in background")

        # Start sensors
        self._start_sensors()

        logger.info("Minion started")

    def stop(self) -> None:
        """Stop the minion."""
        with self._lock:
            if not self._running:
                return
            self._running = False

        # Stop sensors
        for sensor in self._sensors:
            sensor.stop()
        self._sensors.clear()

        # Disconnect MQTT
        if self._mqtt:
            self._mqtt.disconnect()
            self._mqtt = None

        logger.info("Minion stopped")

    def _handle_signal(self, signum: int, frame: Any) -> None:
        """Handle shutdown signals."""
        logger.info(f"Received signal {signum}")
        self.stop()

    def _start_sensors(self) -> None:
        """Start all enabled sensors."""
        def on_event(event: SensorEvent) -> None:
            """Handle sensor event."""
            if self._mqtt:
                self._mqtt.publish_event(event.event)

        session_id = self._config.minion_id or "unknown"

        # Screen activity sensor
        screen_settings = self._config.sensors.get("screen_activity")
        if screen_settings and screen_settings.enabled:
            screen_sensor = ScreenSensor(
                settings=screen_settings,
                on_event=on_event,
                session_id=session_id,
                user_account=None,
            )
            self._sensors.append(screen_sensor)
            screen_sensor.start()

        # Application focus sensor
        focus_settings = self._config.sensors.get("application_focus")
        if focus_settings and focus_settings.enabled:
            focus_sensor = ApplicationFocusSensor(
                settings=focus_settings,
                on_event=on_event,
                session_id=session_id,
            )
            self._sensors.append(focus_sensor)
            focus_sensor.start()

        # Keyboard activity sensor
        keyboard_settings = self._config.sensors.get("keyboard_activity")
        if keyboard_settings and keyboard_settings.enabled:
            keyboard_sensor = KeyboardSensor(
                settings=keyboard_settings,
                on_event=on_event,
                window_duration=keyboard_settings.sampling_interval,
            )
            self._sensors.append(keyboard_sensor)
            keyboard_sensor.start()

        # Battery sensor
        battery_settings = self._config.sensors.get("battery")
        if battery_settings and battery_settings.enabled:
            battery_sensor = BatterySensor(
                settings=battery_settings,
                on_event=on_event,
            )
            self._sensors.append(battery_sensor)
            battery_sensor.start()

        # Network sensor
        network_settings = self._config.sensors.get("network_status")
        if network_settings and network_settings.enabled:
            network_sensor = NetworkSensor(
                settings=network_settings,
                on_event=on_event,
            )
            self._sensors.append(network_sensor)
            network_sensor.start()

        logger.info(f"Started {len(self._sensors)} sensors")

    def _on_connect(self) -> None:
        """Called when MQTT connects."""
        logger.info("Connected to Cortex")

    def _on_disconnect(self) -> None:
        """Called when MQTT disconnects."""
        logger.warning("Disconnected from Cortex, events will be queued")


if __name__ == "__main__":
    cli()
