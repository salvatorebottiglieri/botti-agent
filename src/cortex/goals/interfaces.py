"""Goal repository interface - abstract base class."""

from abc import ABC, abstractmethod
from uuid import UUID

from cortex.agentic.models import Goal


class GoalRepository(ABC):
    """
    Abstract interface for goal persistence.

    Implementations should handle database operations.
    """

    @abstractmethod
    async def create(self, goal: Goal) -> Goal:
        """Persist a new goal and return it."""
        ...

    @abstractmethod
    async def get(self, goal_id: UUID) -> Goal | None:
        """Get a goal by ID, or None if it does not exist."""
        ...

    @abstractmethod
    async def update(self, goal: Goal) -> None:
        """Persist changes to an existing goal."""
        ...

    @abstractmethod
    async def list_active(self, limit: int | None = None) -> list[Goal]:
        """List active goals (pending, running, or paused), newest first.

        `limit` bounds the number of rows; None (the default) means no limit.
        """
        ...

    @abstractmethod
    async def get_in_flight(self) -> list[Goal]:
        """List goals left running at shutdown (status == RUNNING)."""
        ...
