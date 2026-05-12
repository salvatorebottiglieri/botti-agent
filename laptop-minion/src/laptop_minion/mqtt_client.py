"""MQTT client for laptop-minion.

Handles connection to the Cortex MQTT broker, event publishing,
reconnection with exponential backoff, and offline queue management.
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable
from uuid import UUID, uuid4

import paho.mqtt.client as mqtt
from paho.mqtt.enums import MQTTErrorCode

from cortex_protocol.schemas import (
    BatchConfig,
    MinionEvent,
    MinionEventBatch,
    MinionEventMetadata,
    MQTTTopics,
    QoS,
)
from laptop_minion.config import Config
from laptop_minion.queue import OfflineQueue

logger = logging.getLogger(__name__)


@dataclass
class ConnectionStatus:
    """Current connection status."""

    connected: bool = False
    connecting: bool = False
    disconnected_at: datetime | None = None
    reconnect_attempts: int = 0
    last_connected_at: datetime | None = None


@dataclass
class EventStats:
    """Statistics for events sent/received."""

    sent: int = 0
    queued: int = 0
    flushed: int = 0
    failed: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# Backoff strategy
# ─────────────────────────────────────────────────────────────────────────────

BACKOFF_BASE_SECONDS = [5, 10, 30, 60]  # 5s, 10s, 30s, 60s
BACKOFF_MAX_SECONDS = 300  # 5 minutes
BACKOFF_JITTER = 0.2  # ±20%


def calculate_backoff(attempt: int) -> float:
    """Calculate backoff delay for a given attempt number."""
    if attempt >= len(BACKOFF_BASE_SECONDS):
        base = BACKOFF_BASE_SECONDS[-1]
    else:
        base = BACKOFF_BASE_SECONDS[attempt]

    # Cap at max
    base = min(base, BACKOFF_MAX_SECONDS)

    # Add jitter
    jitter = base * BACKOFF_JITTER
    return base + random.uniform(-jitter, jitter)


# ─────────────────────────────────────────────────────────────────────────────
# MQTT Client
# ─────────────────────────────────────────────────────────────────────────────


class CortexMQTTClient:
    """MQTT client for communicating with Cortex.

    Features:
    - Automatic reconnection with exponential backoff
    - Event batching with configurable size and flush interval
    - Offline queue for events sent while disconnected
    - Thread-safe event publishing
    """

    def __init__(
        self,
        config: Config,
        on_connect: Callable[[], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
        on_message: Callable[[str, Any], None] | None = None,
    ):
        self._config = config
        self._client_id = f"laptop-minion-{config.minion_id}"
        self._client = mqtt.Client(client_id=self._client_id, protocol=mqtt.MQTTv5)

        # Callbacks
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_message = on_message

        # Connection state
        self._status = ConnectionStatus()
        self._status_lock = threading.Lock()

        # Event batching
        self._batch_config = config.batch
        self._pending_events: list[MinionEvent] = []
        self._sequence = 0
        self._batch_id = uuid4()
        self._flush_timer: threading.Timer | None = None
        self._batch_lock = threading.Lock()

        # Offline queue
        self._queue = OfflineQueue()

        # Reconnection
        self._reconnect_thread: threading.Thread | None = None
        self._should_reconnect = True
        self._reconnect_lock = threading.Lock()

        # Stats
        self._stats = EventStats()
        self._stats_lock = threading.Lock()

        # Setup callbacks
        self._client.on_connect = self._handle_connect
        self._client.on_disconnect = self._handle_disconnect
        self._client.on_message = self._handle_message
        self._client.on_publish = self._handle_publish

        # Set authentication if token provided
        if config.token:
            self._client.username_pw_set("minion", config.token)

        # TLS configuration for secure connections
        if config.broker_url.startswith("mqtts://"):
            self._client.tls_set()

    @property
    def status(self) -> ConnectionStatus:
        """Get current connection status (thread-safe copy)."""
        with self._status_lock:
            return ConnectionStatus(
                connected=self._status.connected,
                connecting=self._status.connecting,
                disconnected_at=self._status.disconnected_at,
                reconnect_attempts=self._status.reconnect_attempts,
                last_connected_at=self._status.last_connected_at,
            )

    @property
    def stats(self) -> EventStats:
        """Get current event statistics."""
        with self._stats_lock:
            return EventStats(
                sent=self._stats.sent,
                queued=self._stats.queued,
                flushed=self._stats.flushed,
                failed=self._stats.failed,
            )

    @property
    def queue_size(self) -> int:
        """Get number of events in offline queue."""
        return self._queue.size()

    def connect(self, timeout: float = 30) -> bool:
        """Connect to the MQTT broker.

        Returns True if connection succeeds, False otherwise.
        """
        # Parse broker URL
        uri = self._config.broker_url
        if uri.startswith("mqtt://"):
            host_port = uri[7:]
        elif uri.startswith("mqtts://"):
            host_port = uri[8:]
        else:
            host_port = uri

        if ":" in host_port:
            host, port_str = host_port.rsplit(":", 1)
            port = int(port_str)
        else:
            host = host_port
            port = 8883 if uri.startswith("mqtts") else 1883

        with self._status_lock:
            self._status.connecting = True

        logger.info(f"Connecting to {host}:{port}...")

        try:
            result = self._client.connect(host, port, keepalive=60)
            if result != MQTTErrorCode.MQTT_SUCCESS:
                logger.error(f"MQTT connection failed: {result}")
                with self._status_lock:
                    self._status.connecting = False
                return False

            # Start the network loop in a thread
            self._client.loop_start()

            # Wait for connection with timeout
            start = time.time()
            while time.time() - start < timeout:
                with self._status_lock:
                    if self._status.connected:
                        return True
                time.sleep(0.1)

            logger.warning("Connection timeout reached")
            return False

        except Exception as e:
            logger.error(f"Connection error: {e}")
            with self._status_lock:
                self._status.connecting = False
            return False

    def disconnect(self) -> None:
        """Disconnect from the MQTT broker."""
        with self._reconnect_lock:
            self._should_reconnect = False

        # Cancel any pending flush
        if self._flush_timer:
            self._flush_timer.cancel()
            self._flush_timer = None

        # Flush remaining events
        self._flush_batch(force=True)

        self._client.loop_stop()
        self._client.disconnect()
        logger.info("Disconnected from broker")

    def publish_event(self, event: MinionEvent) -> None:
        """Publish a single event (queued for batching).

        Thread-safe.
        """
        with self._batch_lock:
            self._pending_events.append(event)
            self._sequence += 1

            # Check if we should flush now
            if len(self._pending_events) >= self._batch_config.max_size:
                self._flush_batch_locked(force=True)

        # Reset/chedule flush timer
        self._schedule_flush()

    def _schedule_flush(self) -> None:
        """Schedule a flush after the configured interval."""
        with self._batch_lock:
            if self._flush_timer:
                self._flush_timer.cancel()

            interval = self._batch_config.flush_interval
            self._flush_timer = threading.Timer(interval, self._on_flush_timer)
            self._flush_timer.daemon = True
            self._flush_timer.start()

    def _on_flush_timer(self) -> None:
        """Called when flush timer expires."""
        with self._batch_lock:
            if self._pending_events:
                self._flush_batch_locked(force=True)

    def _flush_batch(self, force: bool = False) -> None:
        """Flush pending events to MQTT or queue."""
        with self._batch_lock:
            self._flush_batch_locked(force=force)

    def _flush_batch_locked(self, force: bool = False) -> None:
        """Flush pending events (must hold _batch_lock)."""
        if not self._pending_events:
            return

        events_to_send = self._pending_events
        self._pending_events = []
        self._batch_id = uuid4()
        start_sequence = self._sequence - len(events_to_send)

        # Cancel flush timer
        if self._flush_timer:
            self._flush_timer.cancel()
            self._flush_timer = None

        # Build batch
        metadata = MinionEventMetadata(
            minion_id=UUID(self._config.minion_id),
            minion_type=self._config.minion_type,
            sequence=start_sequence,
            batch_id=self._batch_id,
            device_time=datetime.utcnow(),
        )
        batch = MinionEventBatch(metadata=metadata, events=events_to_send)
        batch_json = batch.model_dump_json()

        with self._status_lock:
            connected = self._status.connected

        if connected:
            # Publish to MQTT
            topic = MQTTTopics.events(self._config.minion_id)
            result = self._client.publish(topic, batch_json, qos=QoS.AT_LEAST_ONCE)
            if result.rc == MQTTErrorCode.MQTT_SUCCESS:
                with self._stats_lock:
                    self._stats.sent += len(events_to_send)
                logger.debug(f"Published {len(events_to_send)} events")
            else:
                logger.warning(f"Publish failed, queuing: {result.rc}")
                self._queue_to_storage(events_to_send, start_sequence)
                with self._stats_lock:
                    self._stats.failed += len(events_to_send)
        else:
            # Queue for later
            self._queue_to_storage(events_to_send, start_sequence)
            with self._stats_lock:
                self._stats.queued += len(events_to_send)
            logger.debug(f"Queued {len(events_to_send)} events (disconnected)")

    def _queue_to_storage(self, events: list[MinionEvent], start_sequence: int) -> None:
        """Queue events to offline storage."""
        self._queue.enqueue_batch(events, self._batch_id, start_sequence)

    def _flush_offline_queue(self) -> None:
        """Flush queued events to MQTT."""
        if self._queue.size() == 0:
            return

        events, batch_id = self._queue.flush()
        if not events:
            return

        # Build batch
        queue_ids = []
        minion_events = []
        sequences = []

        for queue_id, event, sequence in events:
            queue_ids.append(queue_id)
            minion_events.append(event)
            sequences.append(sequence)

        if not minion_events:
            return

        metadata = MinionEventMetadata(
            minion_id=UUID(self._config.minion_id),
            minion_type=self._config.minion_type,
            sequence=min(sequences),
            batch_id=batch_id,
            device_time=datetime.utcnow(),
        )
        batch = MinionEventBatch(metadata=metadata, events=minion_events)
        batch_json = batch.model_dump_json()

        topic = MQTTTopics.events(self._config.minion_id)
        result = self._client.publish(topic, batch_json, qos=QoS.AT_LEAST_ONCE)

        if result.rc == MQTTErrorCode.MQTT_SUCCESS:
            self._queue.mark_flushed(queue_ids)
            with self._stats_lock:
                self._stats.flushed += len(minion_events)
            logger.info(f"Flushed {len(minion_events)} queued events")
        else:
            logger.warning("Failed to flush offline queue")

    # ─────────────────────────────────────────────────────────────────────────
    # MQTT callbacks
    # ─────────────────────────────────────────────────────────────────────────

    def _handle_connect(self, client: mqtt.Client, userdata: Any, flags: dict, rc: int) -> None:
        """Called when connected to broker."""
        if rc == 0:
            logger.info("Connected to broker")
            with self._status_lock:
                self._status.connected = True
                self._status.connecting = False
                self._status.disconnected_at = None
                self._status.reconnect_attempts = 0
                self._status.last_connected_at = datetime.utcnow()

            # Subscribe to command topics
            command_topic = MQTTTopics.commands(self._config.minion_id)
            client.subscribe(command_topic, qos=QoS.AT_LEAST_ONCE)
            logger.debug(f"Subscribed to {command_topic}")

            # Subscribe to wildcard for register confirmation
            register_topic = MQTTTopics.command_register(self._config.minion_id)
            client.subscribe(register_topic, qos=QoS.AT_LEAST_ONCE)

            # Flush offline queue
            self._flush_offline_queue()

            if self._on_connect:
                self._on_connect()
        else:
            logger.error(f"Connection failed with code {rc}")

    def _handle_disconnect(self, client: mqtt.Client, userdata: Any, rc: int) -> None:
        """Called when disconnected from broker."""
        logger.warning(f"Disconnected from broker (code: {rc})")
        with self._status_lock:
            self._status.connected = False
            self._status.connecting = False
            self._status.disconnected_at = datetime.utcnow()

        if self._on_disconnect:
            self._on_disconnect()

        # Start reconnection if unexpected disconnect
        if rc != 0:
            self._schedule_reconnect()

    def _handle_message(self, client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage) -> None:
        """Called when a message is received."""
        try:
            payload = json.loads(message.payload.decode())
            logger.debug(f"Received message on {message.topic}: {payload}")
            if self._on_message:
                self._on_message(message.topic, payload)
        except Exception as e:
            logger.error(f"Error handling message: {e}")

    def _handle_publish(self, client: mqtt.Client, userdata: Any, mid: int) -> None:
        """Called when a message is published."""
        logger.debug(f"Published message ID: {mid}")

    def _schedule_reconnect(self) -> None:
        """Schedule a reconnection attempt with backoff."""
        with self._reconnect_lock:
            if not self._should_reconnect:
                return

            with self._status_lock:
                attempt = self._status.reconnect_attempts

            delay = calculate_backoff(attempt)
            logger.info(f"Scheduling reconnect in {delay:.1f}s (attempt {attempt + 1})")

            def reconnect_after_delay() -> None:
                time.sleep(delay)
                with self._reconnect_lock:
                    if not self._should_reconnect:
                        return
                self._do_reconnect()

            self._reconnect_thread = threading.Thread(target=reconnect_after_delay, daemon=True)
            self._reconnect_thread.start()

    def _do_reconnect(self) -> None:
        """Perform the actual reconnection."""
        with self._status_lock:
            self._status.reconnect_attempts += 1
            self._status.connecting = True

        try:
            # Parse broker URL again
            uri = self._config.broker_url
            if uri.startswith("mqtt://"):
                host_port = uri[7:]
            elif uri.startswith("mqtts://"):
                host_port = uri[8:]
            else:
                host_port = uri

            if ":" in host_port:
                host, port_str = host_port.rsplit(":", 1)
                port = int(port_str)
            else:
                host = host_port
                port = 8883 if uri.startswith("mqtts") else 1883

            result = self._client.reconnect()
            if result == MQTTErrorCode.MQTT_SUCCESS:
                logger.info("Reconnection initiated")
            else:
                logger.warning(f"Reconnection failed: {result}")
                self._schedule_reconnect()

        except Exception as e:
            logger.error(f"Reconnection error: {e}")
            self._schedule_reconnect()
