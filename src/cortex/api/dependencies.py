"""Dependency injection for FastAPI routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from fastapi import Depends

if TYPE_CHECKING:
    import asyncpg

    from cortex.execution.module import ExecutionModule
    from cortex.interaction.service import InteractionService
    from cortex.llm.base import LLMClient
    from cortex.services.minion_service import MinionService
    from cortex.sessions.interfaces import SessionRepository


# Global app state (set during app startup)
_app_state: dict[str, Any] | None = None


def set_app_state(state: dict[str, Any]) -> None:
    """Set the global app state for dependency injection."""
    global _app_state
    _app_state = state


def get_app_state() -> dict[str, Any]:
    """Get the global app state."""
    if _app_state is None:
        raise RuntimeError("App state not initialized")
    return _app_state


# ─── Service Dependencies ────────────────────────────────────────────────────


async def get_session_repository() -> SessionRepository:
    """Get the session repository."""
    state = get_app_state()
    return cast("SessionRepository", state["session_repository"])


async def get_execution_module() -> ExecutionModule:
    """Get the execution module."""
    state = get_app_state()
    return cast("ExecutionModule", state["execution_module"])


async def get_interaction_service() -> InteractionService:
    """Get the interaction service."""
    state = get_app_state()
    return cast("InteractionService", state["interaction_service"])


async def get_minion_service() -> MinionService | None:
    """Get the minion service."""
    state = get_app_state()
    return cast("MinionService | None", state.get("minion_service"))


async def get_db_pool() -> asyncpg.Pool:
    """Get the database pool."""
    state = get_app_state()
    return cast("asyncpg.Pool", state["db_pool"])


async def get_llm_client() -> LLMClient | None:
    """Get the LLM client."""
    state = get_app_state()
    return cast("LLMClient | None", state.get("llm_client"))


# Type aliases for cleaner route signatures
SessionRepositoryDep = Depends(get_session_repository)
ExecutionModuleDep = Depends(get_execution_module)
InteractionServiceDep = Depends(get_interaction_service)
MinionServiceDep = Depends(get_minion_service)
DbPoolDep = Depends(get_db_pool)
LlmClientDep = Depends(get_llm_client)
