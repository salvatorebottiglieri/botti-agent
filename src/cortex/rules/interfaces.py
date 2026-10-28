"""RuleRepository — the persistence seam of the rule system."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from uuid import UUID

from cortex.rules.models import Rule, RuleState, RuleUse


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
    async def refresh(self, rule: Rule) -> Rule | None:
        """Rewrite a rule's state, confidence, archive and supersede columns.

        Returns None when the row no longer exists (deleted concurrently).
        """
        ...

    @abstractmethod
    async def reassign_uses(self, use_ids: Sequence[int], rule_id: UUID) -> int:
        """Move the given uses to ``rule_id``; return how many rows moved."""
        ...
