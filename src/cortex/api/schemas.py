"""Request/Response schemas for the API."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

# ─── Auth Schemas ──────────────────────────────────────────────────────────────


class TokenCreateRequest(BaseModel):
    """Request to create a new API token."""

    name: str = Field(..., description="Name for this token")


class TokenResponse(BaseModel):
    """Response with the created token."""

    token: str = Field(..., description="The API token (shown only once)")
    name: str
    created_at: datetime


class TokenListItem(BaseModel):
    """Token info for listing (no secret)."""

    id: UUID
    name: str
    created_at: datetime
    last_used_at: datetime | None = None
    revoked: bool = False


# ─── Chat Schemas ─────────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    """Request for chat completion."""

    message: str = Field(..., description="User message", min_length=1)
    session_id: UUID | None = Field(None, description="Existing session ID")
    mode: Literal["chat", "goal"] = Field("chat", description="Execution mode")
    max_iterations: int | None = Field(None, description="Max iterations (default: 20)")


class ChatResponse(BaseModel):
    """Response from chat completion."""

    session_id: UUID
    message: str
    iterations: int = 0
    tools_used: list[str] = Field(default_factory=list)


# ─── Session Schemas ──────────────────────────────────────────────────────────


class MessageCreate(BaseModel):
    """Create a message in a session."""

    role: Literal["user", "assistant", "tool_result"] = Field(..., description="Message role")
    content: str = Field(..., description="Message content", min_length=1)
    tool_calls: list[dict[str, Any]] | None = Field(None, description="Tool calls if any")


class SessionResponse(BaseModel):
    """Session with its messages."""

    id: UUID
    state: str
    created_at: datetime
    last_activity_at: datetime
    ended_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionWithMessages(BaseModel):
    """Session with full conversation history."""

    session: SessionResponse
    messages: list["MessageResponse"]


class MessageResponse(BaseModel):
    """A message in a conversation."""

    id: UUID
    role: str
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    created_at: datetime


# ─── Goal Schemas ─────────────────────────────────────────────────────────────


class GoalCreateRequest(BaseModel):
    """Request to create a goal."""

    description: str = Field(..., description="Goal description", min_length=1)
    priority: Literal["low", "normal", "high"] = Field("normal", description="Priority")
    deadline: datetime | None = Field(None, description="Optional deadline")


class GoalResponse(BaseModel):
    """Response for a goal."""

    id: UUID
    description: str
    status: str
    priority: str
    created_at: float | None = None
    started_at: float | None = None
    completed_at: float | None = None
    error: str | None = None
    steps: list["GoalStepResponse"] = Field(default_factory=list)


class GoalStepResponse(BaseModel):
    """A step within a goal."""

    step_number: int
    action: str
    result: str | None = None
    error: str | None = None


class GoalResultResponse(BaseModel):
    """Result of goal execution."""

    goal_id: UUID
    success: bool
    message: str
    iterations: int = 0
    error: str | None = None


# ─── Minion Schemas ───────────────────────────────────────────────────────────


class MinionResponse(BaseModel):
    """Minion info."""

    id: UUID
    minion_id: str
    minion_type: str
    minion_version: str | None = None
    registered_at: datetime
    last_heartbeat_at: datetime | None = None
    last_known_ip: str | None = None
    state: str = "unknown"
    metadata: dict[str, Any] = Field(default_factory=dict)


class MinionTokenRequest(BaseModel):
    """Request to generate a minion token."""

    name: str = Field(..., description="Token name/description")


class MinionTokenResponse(BaseModel):
    """Response with minion token."""

    token: str
    minion_id: str
    created_at: datetime


class MinionConfigRequest(BaseModel):
    """Request to push config to a minion."""

    config: dict[str, Any] = Field(..., description="Configuration to push")


# ─── Health Schemas ───────────────────────────────────────────────────────────


class HealthStatus(BaseModel):
    """Health status of a component."""

    status: Literal["healthy", "unhealthy", "unknown"]
    latency_ms: float | None = None
    error: str | None = None


class HealthResponse(BaseModel):
    """Overall health response."""

    status: Literal["healthy", "unhealthy"]
    components: dict[str, HealthStatus] = Field(default_factory=dict)
    version: str
    timestamp: datetime


# ─── Error Schemas ───────────────────────────────────────────────────────────


class ErrorResponse(BaseModel):
    """Standard error response."""

    error: str = Field(..., description="Error type")
    detail: str = Field(..., description="Error details")


# Update forward references
SessionWithMessages.model_rebuild()
GoalResponse.model_rebuild()
