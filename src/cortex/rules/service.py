"""RuleService — the lifecycle mechanics of the rule system.

Create (dictated / from a correction), record a use, sweep (list_active),
archive, split, merge. The score itself is pure and lives in ``scoring``; this
module owns the ordering of the writes and the rule that a stored confidence is
always the recomputed posterior over the rule's active uses.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from uuid import UUID

from cortex.rules.constants import CONTEXT_BUDGET, PRIOR
from cortex.rules.interfaces import RuleRepository
from cortex.rules.models import (
    Rule,
    RuleNotFoundError,
    RuleOrigin,
    RuleSpec,
    RuleState,
    RuleUse,
    UseOutcome,
)
from cortex.rules.scoring import next_state, posterior

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RuleService:
    """The rule lifecycle over a RuleRepository.

    ``clock`` is injectable so the 6-month window and the timestamps are
    deterministic in tests.
    """

    def __init__(
        self, repository: RuleRepository, *, clock: Callable[[], datetime] = _utcnow
    ) -> None:
        self._repository = repository
        self._clock = clock

    async def create_dictated(self, session_id: UUID, spec: RuleSpec) -> Rule:
        """Create a rule the user dictated: definitive, with the dictation as
        its first `approval` use (sigmoid(ln 20) ~= 0.952)."""
        _require_text(spec)
        now = self._clock()
        rule = Rule(
            condition=spec.condition,
            action=spec.action,
            rationale=spec.rationale,
            state=RuleState.DEFINITIVE,
            origin=RuleOrigin.DICTATED,
        )
        # The dictation is evidence about the rule it dictates; its situation is
        # the rule's own condition, there being no request to apply it to.
        dictation = RuleUse(
            rule_id=rule.id,
            session_id=session_id,
            situation=spec.condition,
            outcome=UseOutcome.APPROVAL,
            occurred_at=now,
        )
        rule.confidence = posterior(PRIOR, [dictation], now)
        stored = await self._repository.create(rule)
        await self._repository.add_use(dictation)
        return stored

    async def create_from_correction(self, session_id: UUID, spec: RuleSpec) -> Rule:
        """Create a rule a correction revealed: staging, prior confidence, no use.

        ``session_id`` is accepted for symmetry with ``create_dictated``; a
        correction-born rule records no evidence until it is applied.
        """
        del session_id
        _require_text(spec)
        rule = Rule(
            condition=spec.condition,
            action=spec.action,
            rationale=spec.rationale,
            state=RuleState.STAGING,
            confidence=PRIOR,
            origin=RuleOrigin.CORRECTION,
        )
        return await self._repository.create(rule)

    async def record_use(
        self,
        rule_id: UUID,
        session_id: UUID,
        situation: str,
        outcome: UseOutcome,
    ) -> Rule:
        """Append one use and persist the recomputed confidence and state."""
        rule = await self._repository.get(rule_id)
        if rule is None:
            raise RuleNotFoundError(str(rule_id))

        now = self._clock()
        await self._repository.add_use(
            RuleUse(
                rule_id=rule_id,
                session_id=session_id,
                situation=situation,
                outcome=outcome,
                occurred_at=now,
            )
        )
        # Recomputed from the whole active set, never from the previous score:
        # that is what makes an expired use stop contributing.
        uses = await self._repository.list_uses(rule_id)
        rule.confidence = posterior(PRIOR, uses, now)
        rule.state = next_state(rule.state, rule.confidence, uses, now)
        _stamp_archive(rule, now)
        refreshed = await self._repository.refresh(rule)
        if refreshed is None:
            raise RuleNotFoundError(str(rule_id))
        return refreshed

    async def list_active(self) -> list[Rule]:
        """The rules that may enter the context: staging + definitive, highest
        confidence first, at most CONTEXT_BUDGET.

        Sweeping here is what applies expiry, promotion and demotion: a rule
        whose recomputed state or confidence differs from the stored one is
        refreshed before it is returned.
        """
        candidates = await self._repository.list_by_states(
            [RuleState.STAGING, RuleState.DEFINITIVE], limit=CONTEXT_BUDGET
        )
        now = self._clock()
        active: list[Rule] = []
        for candidate in candidates:
            uses = await self._repository.list_uses(candidate.id)
            confidence = posterior(PRIOR, uses, now)
            state = next_state(candidate.state, confidence, uses, now)
            rule = candidate
            if state is not candidate.state or confidence != candidate.confidence:
                candidate.confidence = confidence
                candidate.state = state
                _stamp_archive(candidate, now)
                refreshed = await self._repository.refresh(candidate)
                if refreshed is None:
                    logger.warning(f"Rule {candidate.id} vanished during the sweep")
                    continue
                rule = refreshed
            if rule.state is RuleState.ARCHIVED:
                continue
            active.append(rule)
        return active[:CONTEXT_BUDGET]

    async def get(self, rule_id: UUID) -> Rule | None:
        """Return one rule whatever its state — the CLI shows archived ones too."""
        return await self._repository.get(rule_id)

    async def archive(self, rule_id: UUID) -> Rule | None:
        """Retire a rule on the user's command."""
        rule = await self._repository.get(rule_id)
        if rule is None:
            return None
        now = self._clock()
        rule.state = RuleState.ARCHIVED
        rule.archived_at = now
        return await self._repository.refresh(rule)

    async def split(
        self,
        rule_id: UUID,
        children: Sequence[RuleSpec],
        assignments: Mapping[int, int],
    ) -> list[Rule]:
        """Replace a rule with two or more complementary ones.

        ``assignments`` maps each of the parent's use ids to the index of the
        child that inherits it — the classifier's decision in slice B. Every
        use must be assigned exactly once: a use left on the archived parent
        would silently stop scoring. Child 0 inherits the parent's state; the
        others start in staging.
        """
        if len(children) < 2:
            raise ValueError("a split needs at least two children")
        for spec in children:
            _require_text(spec)
        parent = await self._repository.get(rule_id)
        if parent is None:
            raise RuleNotFoundError(str(rule_id))

        uses = await self._repository.list_uses(rule_id)
        # Validated before the first write: a rejected split leaves no child
        # behind and no use detached from its rule.
        assigned: list[tuple[RuleUse, int]] = []
        for use in uses:
            if use.id is None:
                raise ValueError("a split needs persisted uses (a use without an id)")
            child_index = assignments.get(use.id)
            if child_index is None:
                raise ValueError(f"use {use.id} is not assigned to any child")
            if not 0 <= child_index < len(children):
                raise ValueError(
                    f"use {use.id} is assigned to child {child_index}, "
                    f"out of range for {len(children)} children"
                )
            assigned.append((use, child_index))

        now = self._clock()
        created: list[Rule] = []
        for index, spec in enumerate(children):
            inherited = [use for use, assigned_index in assigned if assigned_index == index]
            created.append(
                await self._repository.create(
                    Rule(
                        condition=spec.condition,
                        action=spec.action,
                        rationale=spec.rationale,
                        state=parent.state if index == 0 else RuleState.STAGING,
                        confidence=posterior(PRIOR, inherited, now),
                        origin=RuleOrigin.CORRECTION,
                        created_at=now,
                        updated_at=now,
                    )
                )
            )

        for index, child in enumerate(created):
            use_ids = [
                use.id for use, assigned_index in assigned if assigned_index == index and use.id is not None
            ]
            await self._repository.reassign_uses(use_ids, child.id)

        parent.state = RuleState.ARCHIVED
        parent.archived_at = now
        parent.superseded_by = created[0].id
        await self._repository.refresh(parent)
        return created

    async def merge(self, parent_ids: Sequence[UUID], spec: RuleSpec) -> Rule:
        """Fuse several rules into one that takes the union of their uses.

        Every parent must exist. The fused rule is definitive only if every
        parent was; the parents are archived and point at it.
        """
        if len(parent_ids) < 2:
            raise ValueError("a merge needs at least two parents")
        _require_text(spec)
        parents: list[Rule] = []
        for parent_id in parent_ids:
            parent = await self._repository.get(parent_id)
            if parent is None:
                raise RuleNotFoundError(str(parent_id))
            parents.append(parent)

        uses: list[RuleUse] = []
        for parent in parents:
            uses.extend(await self._repository.list_uses(parent.id))

        now = self._clock()
        fused = await self._repository.create(
            Rule(
                condition=spec.condition,
                action=spec.action,
                rationale=spec.rationale,
                state=(
                    RuleState.DEFINITIVE
                    if all(parent.state is RuleState.DEFINITIVE for parent in parents)
                    else RuleState.STAGING
                ),
                confidence=PRIOR,
                origin=RuleOrigin.MERGE,
                created_at=now,
                updated_at=now,
            )
        )
        await self._repository.reassign_uses(
            [use.id for use in uses if use.id is not None], fused.id
        )

        fused.confidence = posterior(PRIOR, uses, now)
        refreshed = await self._repository.refresh(fused)
        if refreshed is not None:
            fused = refreshed

        for parent in parents:
            parent.state = RuleState.ARCHIVED
            parent.archived_at = now
            parent.superseded_by = fused.id
            await self._repository.refresh(parent)
        return fused


def _require_text(spec: RuleSpec) -> None:
    """A rule needs a condition and an action; whitespace is not a condition."""
    if not spec.condition.strip():
        raise ValueError("a rule needs a condition")
    if not spec.action.strip():
        raise ValueError("a rule needs an action")


def _stamp_archive(rule: Rule, now: datetime) -> None:
    """Set archived_at the moment a sweep archives a rule, once."""
    if rule.state is RuleState.ARCHIVED and rule.archived_at is None:
        rule.archived_at = now
