"""Cortex Agentic Module.

Provides the core agentic loop: Think → Act → Observe → Respond.
"""

from cortex.agentic.context_builder import ContextBuilder
from cortex.agentic.events import (
    ErrorEvent,
    LoopEvent,
    ResponseDoneEvent,
    TextDeltaEvent,
    ThinkingEvent,
    ToolResultEvent,
    ToolStartEvent,
)
from cortex.agentic.executor import LoopExecutor
from cortex.agentic.loop import AgentLoop
from cortex.agentic.models import (
    AmbientContext,
    ChatResponse,
    Context,
    Decision,
    DecisionType,
    Goal,
    GoalContext,
    GoalResult,
    GoalStatus,
    GoalStep,
    MaxIterationsError,
    MemoryContext,
    Mode,
    PersonalityContext,
)
from cortex.agentic.reasoner import Reasoner

__all__ = [
    # Enums
    "Mode",
    "DecisionType",
    "GoalStatus",
    # Models
    "Decision",
    "Context",
    "GoalContext",
    "PersonalityContext",
    "AmbientContext",
    "MemoryContext",
    "ChatResponse",
    "Goal",
    "GoalStep",
    "GoalResult",
    "MaxIterationsError",
    # Streaming
    "ErrorEvent",
    "LoopEvent",
    "ResponseDoneEvent",
    "TextDeltaEvent",
    "ThinkingEvent",
    "ToolResultEvent",
    "ToolStartEvent",
    # Core
    "ContextBuilder",
    "Reasoner",
    "LoopExecutor",
    "AgentLoop",
]
