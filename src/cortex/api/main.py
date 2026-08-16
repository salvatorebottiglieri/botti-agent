"""Main FastAPI application - wires all routes and services."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from cortex.api.dependencies import set_app_state
from cortex.api.routes import (
    admin_auth_router,
    chat_router,
    goals_router,
    health_router,
    minions_router,
    sessions_router,
)
from cortex.config.loader import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan handler for startup/shutdown."""
    # Startup
    logger.info("Starting Cortex API...")

    # Import and set up app state
    from cortex.api.dependencies import get_app_state

    try:
        get_app_state()
        logger.info("App state loaded successfully")
    except RuntimeError:
        logger.warning("App state not initialized - running in standalone mode")

    yield

    # Shutdown
    logger.info("Shutting down Cortex API...")


def create_api_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns a fully wired FastAPI app with all routes mounted.
    """
    settings = get_settings()

    app = FastAPI(
        title="Cortex API",
        description="Personal AI Assistant - Cortex",
        version=settings.version,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Configure appropriately for production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Global exception handler
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error(f"Unhandled exception: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "detail": str(exc)},
        )

    # Mount routes
    # Health check (no prefix, unauthenticated)
    app.include_router(health_router)

    # Admin routes (auth handled per-route)
    app.include_router(admin_auth_router)

    # Protected routes
    app.include_router(chat_router)
    app.include_router(sessions_router)
    app.include_router(goals_router)
    app.include_router(minions_router)

    # Root endpoint
    @app.get("/", tags=["root"])
    async def root() -> dict[str, Any]:
        """Root endpoint - API info."""
        return {
            "name": "Cortex API",
            "version": settings.version,
            "docs": "/docs",
        }

    # Minimal web UI (single self-contained page, no build step)
    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.is_dir():
        app.mount("/ui", StaticFiles(directory=static_dir, html=True), name="ui")

    return app


def create_app() -> FastAPI:
    """Alias for create_api_app for backward compatibility."""
    return create_api_app()


def bootstrap_app(state: dict[str, Any]) -> FastAPI:
    """
    Bootstrap the app with full dependency injection.

    Called during startup with all initialized services.

    Args:
        state: Dict containing all initialized services:
            - db_pool
            - event_bus
            - session_service
            - execution_module
            - interaction_service
            - minion_service
            - context_provider
            - fact_store
            - llm_client
    """
    # Set the app state for dependency injection
    set_app_state(state)

    return create_app()
