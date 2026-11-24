"""Cortex Agentic Module.

Provides the core agentic loop: Think → Act → Observe → Respond.
"""

from cortex.agentic.context_builder import ContextBuilder
from cortex.agentic.executor import LoopExecutor
from cortex.agentic.loop import AgentLoop
from cortex.agentic.models import (
    ChatResponse,
    Context,
    Decision,
    DecisionType,
    GoalContext,
    GoalResult,
    MaxIterationsError,
    Mode,
)
from cortex.agentic.reasoner import Reasoner

__all__ = [
    # Enums
    "Mode",
    "DecisionType",
    # Models
    "Decision",
    "Context",
    "GoalContext",
    "ChatResponse",
    "GoalResult",
    "MaxIterationsError",
    # Core
    "ContextBuilder",
    "Reasoner",
    "LoopExecutor",
    "AgentLoop",
]
