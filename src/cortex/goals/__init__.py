"""Goal persistence — repository interface and implementations."""

from cortex.goals.interfaces import GoalRepository
from cortex.goals.repository import InMemoryGoalRepository, PostgresGoalRepository

__all__ = [
    "GoalRepository",
    "InMemoryGoalRepository",
    "PostgresGoalRepository",
]
