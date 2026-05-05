"""Cortex API Routes."""

from cortex.api.routes import admin_auth, chat, goals, health, minions, sessions

# Export routers for inclusion in FastAPI app
health_router = health.router
chat_router = chat.router
sessions_router = sessions.router
goals_router = goals.router
minions_router = minions.router
admin_auth_router = admin_auth.router

__all__ = [
    "health_router",
    "chat_router",
    "sessions_router",
    "goals_router",
    "minions_router",
    "admin_auth_router",
]
