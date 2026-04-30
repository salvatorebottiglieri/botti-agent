"""MQTT client for receiving minion events."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Callable

import aiomqtt

from .interfaces import MinionGateway, MinionEventHandler
from .models import MinionEvent, MinionEventBatch, MinionConfig

logger = logging.getLogger(__name__)


class MinionMQTTClient(MinionGateway):
    """
    MQTT client for receiving minion events.

    Connects to the MQTT broker and forwards events to handlers.
    """

    def __init__(self, config: MinionConfig) -> None:
        """
        Initialize the MQTT client.

        Args:
            config: Minion configuration including broker URL and topics.
        """
        self._config = config
        self._client: aiomqtt.Client | None = None
        self._handlers: list[MinionEventHandler] = []
        self._connected = False
        self._tasks: list[asyncio.Task] = []
        self._loop = asyncio.get_event_loop()

    async def connect(self) -> None:
        """Connect to the MQTT broker."""
        try:
            logger.info(
                "Connecting to MQTT broker",
                extra={"broker": self._config.broker_url},
            )

            # Create client with optional auth
            if self._config.username and self._config.password:
                self._client = aiomqtt.Client(
                    identifier=self._config.minion_id,
                    username=self._config.username,
                    password=self._config.password,
                )
            else:
                self._client = aiomqtt.Client(
                    identifier=self._config.minion_id,
                )

            # Connect
            host = self._config.broker_url.replace("mqtt://", "")
            port = self._config.port
            await self._client.connect(host, port, keepalive=self._config.keepalive)

            self._connected = True
            logger.info(
                "Connected to MQTT broker",
                extra={"broker": host, "port": port},
            )

            # Start listening task
            self._tasks.append(asyncio.create_task(self._listen()))

        except Exception as e:
            logger.error("Failed to connect to MQTT broker", extra={"error": str(e)})
            raise

    async def disconnect(self) -> None:
        """Disconnect from the MQTT broker."""
        logger.info("Disconnecting from MQTT broker")

        # Cancel listening tasks
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()

        # Disconnect client
        if self._client:
            await self._client.disconnect()
            self._client = None

        self._connected = False

    async def subscribe(self, handler: MinionEventHandler) -> None:
        """
        Subscribe to minion events with a handler.

        Args:
            handler: The handler to receive events.
        """
        if handler not in self._handlers:
            self._handlers.append(handler)

        # Also subscribe to topics if client is connected
        if self._client and self._connected:
            for topic in self._config.topics:
                await self._client.subscribe(topic, qos=self._config.qos)
                logger.info("Subscribed to topic", extra={"topic": topic})

    async def unsubscribe(self, handler: MinionEventHandler) -> None:
        """
        Unsubscribe a handler from events.

        Args:
            handler: The handler to remove.
        """
        if handler in self._handlers:
            self._handlers.remove(handler)

    def is_connected(self) -> bool:
        """Check if the gateway is currently connected."""
        return self._connected

    async def _listen(self) -> None:
        """Listen for messages on subscribed topics."""
        if not self._client:
            return

        logger.info("Starting MQTT listener", extra={"topics": self._config.topics})

        try:
            async for message in self._client.messages():
                await self._handle_message(message)
        except asyncio.CancelledError:
            logger.info("MQTT listener cancelled")
        except Exception as e:
            logger.error("MQTT listener error", extra={"error": str(e)})

    async def _handle_message(self, message: aiomqtt.Message) -> None:
        """
        Handle an incoming MQTT message.

        Args:
            message: The MQTT message to process.
        """
        try:
            topic = message.topic
            payload_str = message.payload.decode("utf-8")

            logger.debug(
                "Received MQTT message",
                extra={"topic": str(topic), "payload": payload_str[:100]},
            )

            # Parse the message
            try:
                data = json.loads(payload_str)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON in MQTT message", extra={"payload": payload_str})
                return

            # Extract minion ID from topic (format: minions/{minion_id}/events)
            topic_str = str(topic)
            minion_id = self._extract_minion_id(topic_str)

            if minion_id:
                # Create event from message
                event = self._parse_event(minion_id, data, topic_str)
                if event:
                    await self._dispatch_event(event)

        except Exception as e:
            logger.error(
                "Error handling MQTT message",
                extra={"error": str(e)},
            )

    def _extract_minion_id(self, topic: str) -> str | None:
        """Extract minion ID from topic."""
        parts = topic.split("/")
        if len(parts) >= 2 and parts[0] == "minions":
            return parts[1]
        return None

    def _parse_event(
        self, minion_id: str, data: dict, topic: str
    ) -> MinionEvent | MinionEventBatch | None:
        """Parse message data into an event or batch."""
        # Check if this is a batch
        if "events" in data and isinstance(data["events"], list):
            events = []
            for e in data["events"]:
                event = MinionEvent.create(
                    minion_id=minion_id,
                    event_type=e.get("event_type", "custom.event"),
                    payload=e.get("payload", {}),
                )
                events.append(event)
            return MinionEventBatch.create(minion_id, events)

        # Single event
        return MinionEvent.create(
            minion_id=minion_id,
            event_type=data.get("event_type", "custom.event"),
            payload=data.get("payload", data),
        )

    async def _dispatch_event(self, event: MinionEvent | MinionEventBatch) -> None:
        """Dispatch event(s) to handlers."""
        for handler in self._handlers:
            try:
                if isinstance(event, MinionEventBatch):
                    await handler.handle_batch(event)
                else:
                    await handler.handle_event(event)
            except Exception as e:
                logger.error(
                    "Handler error",
                    extra={"handler": type(handler).__name__, "error": str(e)},
                )