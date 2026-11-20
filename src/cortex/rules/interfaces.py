"""The rule system's two persistence seams: RuleRepository and FeedbackQueue."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from cortex.rules.models import Rule, RuleState, RuleUse

if TYPE_CHECKING:
    from cortex.rules.queue import QueuedFeedback


class RuleRepository(ABC):
    """Persistence primitives for ``rules`` and ``rule_uses``.

    The repository knows nothing about the score: it stores the confidence and
    state it is handed and returns the row it wrote. ``refresh`` is the only
    method that rewrites a rule, so a confidence that was never refreshed is
    never persisted.
    """

    @abstractmethod
    async def create(self, rule: Rule) -> Rule:
        """Insert one rule and return the stored row."""
        ...

    @abstractmethod
    async def get(self, rule_id: UUID) -> Rule | None:
        """Return one rule, or None when no row matches."""
        ...

    @abstractmethod
    async def add_use(self, use: RuleUse) -> RuleUse:
        """Append one use and return it with its generated id."""
        ...

    @abstractmethod
    async def list_uses(self, rule_id: UUID) -> list[RuleUse]:
        """List a rule's uses, oldest first."""
        ...

    @abstractmethod
    async def list_by_states(
        self, states: Sequence[RuleState], *, limit: int = 500
    ) -> list[Rule]:
        """List rules in any of ``states``, highest confidence first."""
        ...

    @abstractmethod
    async def list_children(self, rule_id: UUID) -> list[Rule]:
        """List the rules on either side of ``rule_id``'s history edge.

        For a Split parent, its children carry ``parent_id = rule_id``. For a
        Merge product, its parents carry ``superseded_by = rule_id``. A plain
        rule returns ``[]``.
        """
        ...

    @abstractmethod
    async def refresh(self, rule: Rule) -> Rule | None:
        """Rewrite a rule's state, confidence, archive and supersede columns.

        Returns None when the row no longer exists (deleted concurrently).
        """
        ...

    @abstractmethod
    async def reassign_uses(self, use_ids: Sequence[int], rule_id: UUID) -> None:
        """Move the given uses to ``rule_id``."""
        ...


class FeedbackQueue(ABC):
    """Persistence primitives for ``queued_feedback``.

    The queue holds the turns that had rules in context and got a reply, so
    attribution survives a restart. It has no claim state: idempotency comes
    from the partial unique index on ``rule_uses (feedback_id, rule_id)``, so a
    row picked twice costs a repeated classification and never a wrong score.
    """

    @abstractmethod
    async def enqueue(self, item: QueuedFeedback) -> QueuedFeedback:
        """Store one turn as pending and return the row with its id.

        A blank reply is refused: silence is not evidence.
        """
        ...

    @abstractmethod
    async def list_pending(self, limit: int = 10) -> list[QueuedFeedback]:
        """List the pending rows, oldest first."""
        ...

    @abstractmethod
    async def mark_attributed(self, feedback_id: UUID, now: datetime) -> None:
        """Flip a pending row to attributed; a row already past pending is
        left alone."""
        ...

    @abstractmethod
    async def drop_stale(self, cutoff: datetime) -> int:
        """Drop the pending rows older than ``cutoff``; returns how many."""
        ...
