"""Goal models. ``goals`` owns this model; ``agentic`` and ``execution`` import it from here (ADR-0022)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import UUID, uuid4


class GoalStatus(Enum):
    """Status of a goal."""
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class GoalStep:
    """A single step within a goal execution."""
    step_number: int
    action: str
    result: str | None = None
    error: str | None = None
    started_at: Any | None = None
    completed_at: Any | None = None


@dataclass
class Goal:
    """A goal to be executed by the agent."""
    id: UUID = field(default_factory=uuid4)
    description: str = ""
    status: GoalStatus = GoalStatus.PENDING
    priority: str = "normal"
    steps: list[GoalStep] = field(default_factory=list)
    created_at: Any | None = None
    started_at: Any | None = None
    completed_at: Any | None = None
    deadline: Any | None = None
    error: str | None = None
