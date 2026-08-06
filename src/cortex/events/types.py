"""Event type constants."""

from enum import StrEnum


class EventTypes(StrEnum):
    """Core event types used throughout the system."""

    # ─── User Input ───────────────────────────────────────────
    USER_MESSAGE = "user.message"
    CONVERSATION_MESSAGE = "conversation.message"
    CONVERSATION_ENDED = "conversation.ended"

    # ─── Minion Input (sensory) ───────────────────────────────
    LOCATION = "location"
    PAYMENT = "payment"
    ACTIVITY = "activity"
    CALENDAR = "calendar"
    CALL_LOG = "call_log"
    APP_USAGE = "app_usage"

    # ─── Learning Output ──────────────────────────────────────
    PATTERN_DETECTED = "pattern.detected"
    PREFERENCE_LEARNED = "preference.learned"
    RECOMMENDATION_GENERATED = "recommendation.generated"
    RECOMMENDATION_EXECUTED = "recommendation.executed"

    # ─── Tool/Goal ────────────────────────────────────────────
    TOOL_REQUEST = "tool.request"
    TOOL_RESULT = "tool.result"
    GOAL_CREATED = "goal.created"
    GOAL_STATUS = "goal.status"
    GOAL_COMPLETED = "goal.completed"
    GOAL_FAILED = "goal.failed"
    GOAL_RESUMED = "goal.resumed"

    # ─── Orchestration ────────────────────────────────────────
    MODULE_SPAWN = "module.spawn"
    MODULE_TERMINATE = "module.terminate"

    # ─── Agentic Loop ─────────────────────────────────────────
    LOOP_STARTED = "loop.started"
    LOOP_THOUGHT = "loop.thought"
    LOOP_TOOLS_EXECUTED = "loop.tools_executed"
    LOOP_COMPLETED = "loop.completed"
    LOOP_ERROR = "loop.error"

    # ─── Wildcard ─────────────────────────────────────────────
    ALL = "*"
