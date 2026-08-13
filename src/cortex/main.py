"""
Cortex - Personal AI Assistant

Entry point for the application.
Provides app factory and initialization.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from cortex.config.loader import get_settings
from cortex.config.models import Settings
from cortex.events.base import BaseEvent
from cortex.logging.setup import configure_logging
from cortex.services.tool_executor import ToolExecutorService
from cortex.tools.interfaces import ToolExecutor

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)


@dataclass
class CortexApp:
    """
    Container for all initialized Cortex components.

    Holds references to all services for testing and introspection.
    """

    app: FastAPI | None = field(default=None)
    settings: Settings | None = field(default=None)
    db_pool: Any = field(default=None)
    event_bus: Any = field(default=None)
    session_repository: Any = field(default=None)
    execution_module: Any = field(default=None)
    interaction_service: Any = field(default=None)
    personality_service: Any = field(default=None)
    memory_service: Any = field(default=None)
    minion_service: Any = field(default=None)
    llm_client: Any = field(default=None)

    async def shutdown(self) -> None:
        """Shutdown all components in reverse order."""
        from cortex.db.pool import close_pool

        # Shutdown in reverse order of initialization
        if self.minion_service:
            try:
                await self.minion_service.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting minion service: {e}")

        if self.event_bus:
            try:
                await self.event_bus.stop()
            except Exception as e:
                logger.error(f"Error stopping event bus: {e}")

        if self.db_pool:
            try:
                await close_pool()
            except Exception as e:
                logger.error(f"Error closing DB pool: {e}")

        logger.info("Cortex shutdown complete")


def create_app(cortex_state: dict[str, Any] | None = None) -> FastAPI:
    """
    Create and configure the FastAPI application.

    Args:
        cortex_state: Optional dict of services for dependency injection.
                     If provided, these services are wired into the app.

    Returns:
        Configured FastAPI application.
    """
    from cortex.api.main import create_api_app

    if cortex_state:
        from cortex.api.dependencies import set_app_state
        set_app_state(cortex_state)

    return create_api_app()


async def initialize_app() -> CortexApp:
    """
    Initialize the complete Cortex application.

    Creates all components and wires them together:
    1. Load config
    2. Run DB migrations
    3. Create DB pool
    4. Initialize event bus
    5. Wire repositories → services
    6. Wire services → agentic loop components
    7. Initialize API with all services
    8. Initialize MinionService (if configured)

    Returns:
        CortexApp with all components initialized.
    """

    # 1. Load configuration
    settings = get_settings()

    # Configure logging
    configure_logging(settings)

    logger.info(f"Cortex v{settings.version} - Initializing...")

    # Create the app container
    cortex = CortexApp(settings=settings)


    # 2. Create DB pool
    logger.info("Creating database pool...")
    from cortex.db.pool import create_pool
    cortex.db_pool = await create_pool(settings)

    # 3. Run DB migrations
    logger.info("Running database migrations...")
    from cortex.db.migrations.runner import run_migrations
    await run_migrations()


    # 4. Initialize event bus
    logger.info("Starting event bus...")
    from cortex.events.bus import EventBus
    cortex.event_bus = EventBus()
    await cortex.event_bus.start()

    # 5-7. Create services and wire them
    logger.info("Wiring services...")

    # 5a. Create session repository
    from cortex.sessions.interfaces import SessionRepository
    from cortex.sessions.repository import PostgresSessionRepository

    session_repo: SessionRepository = PostgresSessionRepository()
    cortex.session_repository = session_repo

    # 5b. Create goal repository
    from cortex.goals.interfaces import GoalRepository
    from cortex.goals.repository import PostgresGoalRepository

    goal_repo: GoalRepository = PostgresGoalRepository()

    # 5c. Create memory repositories
    from cortex.memory.interfaces import ConceptRepository, FactExtractor, FactRepository
    from cortex.memory.repository import PostgresConceptRepository, PostgresFactRepository

    fact_repo: FactRepository = PostgresFactRepository()
    concept_repo: ConceptRepository = PostgresConceptRepository()
    memory_extractor: FactExtractor | None = None

    # 5d. Create LLM client
    from cortex.llm.factory import LLMClientFactory
    llm_factory = LLMClientFactory(settings)
    cortex.llm_client = llm_factory.create()

    # 5e. Create tool registry and executor
    from cortex.tools.executor import DefaultToolExecutor
    from cortex.tools.interfaces import ToolRegistry
    from cortex.tools.meta import register_meta_tools
    from cortex.tools.registry import InMemoryToolRegistry

    tool_registry: ToolRegistry = InMemoryToolRegistry()
    register_meta_tools(tool_registry)
    base_executor: ToolExecutor = DefaultToolExecutor(registry=tool_registry)
    tool_executor_service = ToolExecutorService(
        base_executor=base_executor,
        event_bus=cortex.event_bus,
    )
    # Wrap for ToolExecutor interface
    tool_executor: ToolExecutor = _wrap_tool_executor(tool_executor_service)

    # 6. Wire agentic loop components
    from cortex.agentic.context_builder import ContextBuilder
    from cortex.agentic.executor import LoopExecutor
    from cortex.agentic.loop import AgentLoop
    from cortex.agentic.reasoner import Reasoner
    from cortex.execution.module import ExecutionModule

    # Create placeholder memory service first (needed for context builder)
    from cortex.services.memory_service import MemoryService
    cortex.memory_service = MemoryService(
        fact_repository=fact_repo,
        fact_extractor=memory_extractor,
        concept_repository=concept_repo,
        event_bus=cortex.event_bus,
        llm_client=cortex.llm_client,
    )

    context_builder = ContextBuilder(
        session_repository=cortex.session_repository,
        memory_service=cortex.memory_service,
        tool_registry=tool_registry,
    )

    reasoner = Reasoner(
        llm_client=cortex.llm_client,
        tool_registry=tool_registry,
    )

    loop_executor = LoopExecutor(
        tool_executor=tool_executor,
        event_bus=cortex.event_bus,
    )

    agent_loop = AgentLoop(
        context_builder=context_builder,
        reasoner=reasoner,
        executor=loop_executor,
        event_bus=cortex.event_bus,
        session_repository=cortex.session_repository,
    )

    # Create execution module
    cortex.execution_module = ExecutionModule(
        agent_loop=agent_loop,
        event_bus=cortex.event_bus,
        goal_repository=goal_repo,
    )

    # Resume goals left running at a previous shutdown. Startup concern —
    # direct call, not routed through the event bus.
    await cortex.execution_module.resume_in_flight()

    # 5f. Create personality service
    from cortex.interaction.service import PersonalityService
    cortex.personality_service = PersonalityService(memory_service=cortex.memory_service)

    # 5g. Create interaction service
    from cortex.interaction.service import InteractionService
    cortex.interaction_service = InteractionService(
        execution_module=cortex.execution_module,
        session_repository=cortex.session_repository,
        personality_service=cortex.personality_service,
    )

    # 7. Create state dict for API
    state = {
        "db_pool": cortex.db_pool,
        "event_bus": cortex.event_bus,
        "session_repository": cortex.session_repository,
        "execution_module": cortex.execution_module,
        "interaction_service": cortex.interaction_service,
        "personality_service": cortex.personality_service,
        "memory_service": cortex.memory_service,
        "minion_service": None,  # Will be initialized separately
        "llm_client": cortex.llm_client,
        "tool_registry": tool_registry,
    }

    # Create FastAPI app with wired dependencies
    from cortex.api.dependencies import set_app_state
    from cortex.api.main import create_api_app
    set_app_state(state)
    cortex.app = create_api_app()

    # Subscribe services to event bus
    await _subscribe_services(cortex)

    # 8. Initialize MinionService (MQTT client)
    # Note: MinionService is optional and may not be available in all deployments
    try:
        await _initialize_minion_service(cortex)
    except Exception as e:
        logger.warning(f"MinionService not initialized: {e}")
        # Update state with None for minion_service
        state["minion_service"] = None

    logger.info("Cortex initialization complete")
    return cortex


def _wrap_tool_executor(service: ToolExecutorService) -> ToolExecutor:
    """Wrap ToolExecutorService for ToolExecutor interface."""
    from cortex.tools.interfaces import ToolCall, ToolExecutor, ToolResult

    class WrappedExecutor(ToolExecutor):
        async def execute(self, tool_call: ToolCall, *, timeout: int | None = None) -> ToolResult:
            return await service.execute(tool_call, timeout=timeout)

        async def execute_many(
            self, tool_calls: list[ToolCall], *, timeout: int | None = None
        ) -> list[ToolResult]:
            return await service.execute_many(tool_calls, timeout=timeout)

    return WrappedExecutor()


async def _subscribe_services(cortex: CortexApp) -> None:
    """Subscribe all services to the event bus."""
    if not cortex.event_bus:
        return

    # Subscribe execution module
    await cortex.execution_module.subscribe()

    # Subscribe memory service to relevant events
    if cortex.memory_service:
        async def handle_location(event: BaseEvent) -> None:
            await cortex.memory_service.handle_event(event)

        async def handle_activity(event: BaseEvent) -> None:
            await cortex.memory_service.handle_event(event)

        async def handle_calendar(event: BaseEvent) -> None:
            await cortex.memory_service.handle_event(event)

        await cortex.event_bus.subscribe("location", handle_location)
        await cortex.event_bus.subscribe("activity", handle_activity)
        await cortex.event_bus.subscribe("calendar", handle_calendar)

    logger.debug("Services subscribed to event bus")


async def _initialize_minion_service(cortex: CortexApp) -> None:
    """Initialize MinionService for MQTT-based minion communication."""
    from cortex.minions.models import MinionConfig
    from cortex.minions.mqtt_client import MinionMQTTClient
    from cortex.minions.registry import InMemoryMinionRegistry
    from cortex.services.minion_service import MinionService

    logger.info("Initializing MinionService...")

    # Create minion config from settings
    # Note: In production, this would come from config or database
    settings = cortex.settings
    assert settings is not None, "Settings must be initialized before MinionService"
    minion_config = MinionConfig(
        minion_id=f"cortex-{settings.version}",
        minion_name="Cortex Server",
        device_type="server",
        broker_url=settings.mqtt_broker_url,
        username=settings.mqtt_username,
        password=settings.mqtt_password.get_secret_value() if settings.mqtt_password else None,
        topics=["minions/+/events"],  # Subscribe to all minion events
        qos=1,
        keepalive=settings.mqtt_keepalive,
    )

    # Create gateway and registry
    gateway = MinionMQTTClient(config=minion_config)
    registry = InMemoryMinionRegistry()

    # Create and configure service
    cortex.minion_service = MinionService(
        config=minion_config,
        gateway=gateway,
        registry=registry,
        event_bus=cortex.event_bus,
        memory_service=cortex.memory_service,
    )

    # Connect gateway
    await cortex.minion_service.connect()

    logger.info("MinionService initialized")


async def main() -> None:
    """Main entry point."""
    try:
        cortex = await initialize_app()

        # Keep running
        await asyncio.Event().wait()

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        if 'cortex' in locals():
            await cortex.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
