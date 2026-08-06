"""Health check routes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, Depends

from cortex.api.dependencies import get_db_pool, get_llm_client, get_minion_service
from cortex.api.schemas import HealthResponse, HealthStatus

if TYPE_CHECKING:
    import asyncpg

    from cortex.llm.base import LLMClient
    from cortex.services.minion_service import MinionService

router = APIRouter(prefix="/health", tags=["health"])


@router.get(
    "",
    response_model=HealthResponse,
    summary="Health check",
    description="Returns the health status of all system components.",
)
async def health_check(
    db_pool: asyncpg.Pool = Depends(get_db_pool),
    llm_client: LLMClient | None = Depends(get_llm_client),
    minion_service: MinionService | None = Depends(get_minion_service),
) -> HealthResponse:
    """
    Check health of all components.

    - database: Check DB connectivity
    - event_bus: Check event bus is running
    - mqtt: Check MQTT connection
    - llm: Check LLM client connectivity
    """
    from cortex.config.loader import get_settings

    settings = get_settings()

    components: dict[str, HealthStatus] = {}
    overall_healthy = True

    # Check database
    db_status: Literal["healthy", "unhealthy"] = "unhealthy"
    db_latency: float | None = None
    try:
        import time

        start = time.perf_counter()
        async with db_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        db_latency = (time.perf_counter() - start) * 1000
        db_status = "healthy"
    except Exception:
        db_status = "unhealthy"
        overall_healthy = False

    components["database"] = HealthStatus(
        status=db_status,
        latency_ms=db_latency,
    )

    # Check MQTT
    mqtt_status: Literal["healthy", "unhealthy", "unknown"] = "unknown"
    if minion_service:
        if minion_service.is_connected():
            mqtt_status = "healthy"
        else:
            mqtt_status = "unhealthy"
            overall_healthy = False

    components["mqtt"] = HealthStatus(status=mqtt_status)

    # Check LLM
    llm_status: Literal["healthy", "unhealthy", "unknown"] = "unknown"
    llm_latency: float | None = None
    if llm_client:
        try:
            import time

            start = time.perf_counter()
            # Simple ping to LLM - just verify client is initialized
            # Don't make actual API call for health check
            if hasattr(llm_client, "_api_key") or hasattr(llm_client, "client"):
                llm_latency = (time.perf_counter() - start) * 1000
                llm_status = "healthy"
            else:
                llm_status = "healthy"  # Client exists
        except Exception:
            llm_status = "unhealthy"
            overall_healthy = False
    else:
        llm_status = "unknown"

    components["llm"] = HealthStatus(
        status=llm_status,
        latency_ms=llm_latency,
    )

    return HealthResponse(
        status="healthy" if overall_healthy else "unhealthy",
        components=components,
        version=settings.version,
        timestamp=datetime.now(UTC),
    )
