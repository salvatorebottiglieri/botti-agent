"""Agentic core models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from cortex.sessions.models import Message
    from cortex.memory.models import Fact
    from cortex.tools.interfaces import ToolCall, ToolDefinition


class DecisionType(Enum):
    """Types of decisions the agent can make."""
    RESPOND = "respond"           # Done, return text to user
    EXECUTE_TOOLS = "execute_tools"  # Execute tools, continue loop
    ASK_QUESTION = "ask_question"    # Need clarification


class Mode(Enum):
    """Execution mode for the agent."""
    CHAT = "chat"         # Interactive conversation
    GOAL = "goal"         # Background task execution


class GoalStatus(Enum):
    """Status of a goal."""
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Decision:
    """
    Represents a decision made by the reasoning engine.
    
    Attributes:
        decision_type: What type of decision was made
        text: Text response (for RESPOND or ASK_QUESTION)
        tool_calls: Tools to execute (for EXECUTE_TOOLS)
        reasoning: Explanation of why this decision was made
    """
    decision_type: DecisionType
    text: str | None = None
    tool_calls: list[ToolCall] | None = None
    reasoning: str = ""
    
    @classmethod
    def respond(cls, text: str, reasoning: str = "") -> Decision:
        """Create a RESPOND decision."""
        return cls(
            decision_type=DecisionType.RESPOND,
            text=text,
            reasoning=reasoning,
        )
    
    @classmethod
    def execute_tools(cls, tool_calls: list[ToolCall], reasoning: str = "") -> Decision:
        """Create an EXECUTE_TOOLS decision."""
        return cls(
            decision_type=DecisionType.EXECUTE_TOOLS,
            tool_calls=tool_calls,
            reasoning=reasoning,
        )
    
    @classmethod
    def ask_question(cls, question: str, reasoning: str = "") -> Decision:
        """Create an ASK_QUESTION decision."""
        return cls(
            decision_type=DecisionType.ASK_QUESTION,
            text=question,
            reasoning=reasoning,
        )


@dataclass
class PersonalityContext:
    """Personality traits for response formatting."""
    formality: float = 0.5  # 0 = casual, 1 = formal
    verbosity: float = 0.5  # 0 = terse, 1 = verbose
    technical_level: float = 0.5  # 0 = simple, 1 = technical
    humor_level: float = 0.5
    empathy: float = 0.5
    directness: float = 0.5


@dataclass
class AmbientContext:
    """Current ambient context (time, location, activity)."""
    time_of_day: str | None = None  # "morning", "afternoon", "evening", "night"
    day_of_week: str | None = None  # "weekday", "weekend"
    location: str | None = None
    activity: str | None = None
    weather: str | None = None


@dataclass
class MemoryContext:
    """Bundle of everything the Memory Module contributes to a reasoning step.

    `degraded_dimensions` lists the names of dimensions that failed to populate
    ("facts", "personality", "ambient"). Empty when Memory answered fully.
    """
    facts: list[Fact] = field(default_factory=list)
    personality: PersonalityContext | None = None
    ambient: AmbientContext | None = None
    degraded_dimensions: list[str] = field(default_factory=list)


@dataclass
class GoalContext:
    """Context for goal-oriented mode."""
    goal_id: UUID
    description: str
    priority: str = "normal"
    steps_completed: list[str] = field(default_factory=list)
    deadline: Any | None = None


@dataclass
class Context:
    """
    All context needed for LLM reasoning.

    Assembled by ContextBuilder before each reasoning step. Everything the
    Memory Module contributes (facts, personality, ambient, degraded flags)
    lives behind the single `memory: MemoryContext` field so Memory's seam
    has one shape on both producer and consumer sides.
    """
    session_id: UUID
    conversation: list[Message] = field(default_factory=list)
    tools: list[ToolDefinition] = field(default_factory=list)
    memory: MemoryContext = field(default_factory=lambda: MemoryContext())
    goal: GoalContext | None = None


@dataclass
class ChatResponse:
    """Response from a chat interaction."""
    message: str
    iterations: int = 0
    tools_used: list[str] = field(default_factory=list)
    session_id: UUID | None = None
    
    @property
    def tool_names(self) -> list[str]:
        """Get names of tools that were used."""
        return self.tools_used


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


@dataclass
class GoalResult:
    """Result of a goal execution."""
    goal_id: UUID
    success: bool
    message: str
    iterations: int = 0
    steps_completed: list[str] = field(default_factory=list)
    error: str | None = None


class MaxIterationsError(Exception):
    """Raised when the agent loop exceeds max iterations."""
    
    def __init__(self, max_iterations: int):
        self.max_iterations = max_iterations
        super().__init__(f"Agent loop exceeded {max_iterations} iterations")