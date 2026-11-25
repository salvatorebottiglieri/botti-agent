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
            host=settings.app.host,
            port=settings.app.port,
            log_level="info",
        )
        server = uvicorn.Server(config)
        try:
            await server.serve()
        finally:
            # uvicorn has stopped accepting requests and waited out the in-flight
            # ones by the time serve() returns, so this is the release hook.
            try:
                await cortex.shutdown()
            except Exception as shutdown_error:
                # Best-effort: the process is exiting, and a stop failure must not
                # be reported as, or mask, a start failure.
                print(f"Shutdown failed: {shutdown_error}")

    except KeyboardInterrupt:
        print("\nShutting down...")
        sys.exit(0)
    except Exception as e:
        print(f"Failed to start: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run())
