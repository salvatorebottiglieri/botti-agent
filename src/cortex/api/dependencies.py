"""Dependency injection for FastAPI routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends

if TYPE_CHECKING:
    from cortex.execution.module import ExecutionModule
    from cortex.interaction.service import InteractionService, PersonalityService
    from cortex.services.memory_service import MemoryService
    from cortex.services.minion_service import MinionService
    from cortex.sessions.service import SessionService


# Global app state (set during app startup)
_app_state: dict | None = None


def set_app_state(state: dict) -> None:
    """Set the global app state for dependency injection."""
    global _app_state
    _app_state = state


def get_app_state() -> dict:
    """Get the global app state."""
    if _app_state is None:
        raise RuntimeError("App state not initialized")
    return _app_state


# ─── Service Dependencies ────────────────────────────────────────────────────

async def get_session_service() -> SessionService:
    """Get the session service."""
    state = get_app_state()
    return state["session_service"]


async def get_execution_module() -> ExecutionModule:
    """Get the execution module."""
    state = get_app_state()
    return state["execution_module"]


async def get_interaction_service() -> InteractionService:
    """Get the interaction service."""
    state = get_app_state()
    return state["interaction_service"]


async def get_personality_service() -> PersonalityService:
    """Get the personality service."""
    state = get_app_state()
    return state["personality_service"]


async def get_minion_service() -> MinionService:
    """Get the minion service."""
    state = get_app_state()
    return state.get("minion_service")


async def get_memory_service() -> MemoryService:
    """Get the memory service."""
    state = get_app_state()
    return state.get("memory_service")


async def get_db_pool():
    """Get the database pool."""
    state = get_app_state()
    return state["db_pool"]


async def get_llm_client():
    """Get the LLM client."""
    state = get_app_state()
    return state.get("llm_client")


# Type aliases for cleaner route signatures
SessionServiceDep = Depends(get_session_service)
ExecutionModuleDep = Depends(get_execution_module)
InteractionServiceDep = Depends(get_interaction_service)
PersonalityServiceDep = Depends(get_personality_service)
MinionServiceDep = Depends(get_minion_service)
MemoryServiceDep = Depends(get_memory_service)
DbPoolDep = Depends(get_db_pool)
LlmClientDep = Depends(get_llm_client)
