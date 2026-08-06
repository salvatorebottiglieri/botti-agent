"""Entry point for running cortex as a module: python -m cortex."""

import asyncio
import sys

from cortex.main import initialize_app


async def run() -> None:
    """Initialize and run the Cortex application."""
    print("Cortex v0.1.0 - Starting...")

    try:
        # Initialize the app
        cortex = await initialize_app()
        app = cortex.app
        settings = cortex.settings
        assert app is not None and settings is not None

        # Get host/port from settings
        import uvicorn
        config = uvicorn.Config(
            app,
            host=settings.app_host,
            port=settings.app_port,
            log_level="info",
        )
        server = uvicorn.Server(config)
        await server.serve()

    except KeyboardInterrupt:
        print("\nShutting down...")
        sys.exit(0)
    except Exception as e:
        print(f"Failed to start: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run())
