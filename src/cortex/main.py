"""
Cortex - Personal AI Assistant

Entry point for the application.
"""

import asyncio
from cortex.config.loader import get_settings
from cortex.logging.setup import configure_logging


async def main() -> None:
    """Main entry point."""
    # Load configuration
    settings = get_settings()

    # Configure logging
    configure_logging(settings.logging)

    print(f"Cortex v{settings.version}")
    print(f"Database: {settings.database.url}")
    print(f"MQTT: {settings.mqtt.broker_url}")

    # TODO: Start the application
    # - Initialize DB pool and run migrations
    # - Start event bus
    # - Start API server
    # - Connect to MQTT broker
    # - etc.

    print("Cortex started successfully!")

    # Keep running
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
