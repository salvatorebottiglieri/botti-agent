"""AttributionRun — a turn's Feedback becomes uses on the rules it applied (#168)
and, with a ``ConflictCheck``, the rule it proposed (#170).

The run is the only writer of a turn's evidence. It also splits a rule whose
scope correction it attributed (#171): the correction is a use like any other,
and on top of it the rule is cut in two. A classifier or storage failure must
never reach the caller: the turn already happened, and the worst case is one
turn of evidence lost, not a broken reply. A proposal is the cheaper thing to
lose than the uses already written, and a lost split keeps the rule, never a
half-made one.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from uuid import UUID

from cortex.rules.conflicts import ConflictCheck
from cortex.rules.feedback import Feedback, FeedbackClassifier, RuleProposal
from cortex.rules.models import Rule, RuleOrigin, RuleState, UseOutcome
from cortex.rules.service import RuleService
from cortex.rules.splits import SplitProposer

logger = logging.getLogger(__name__)


class AttributionRun:
    """Classifies one turn and records its uses, and its proposal, through the
    ``RuleService``."""

    def __init__(
        self,
        classifier: FeedbackClassifier,
        service: RuleService,
        conflicts: ConflictCheck | None = None,
        splits: SplitProposer | None = None,
    ) -> None:
        self._classifier = classifier
        self._service = service
        # Without a check the proposals are ignored, exactly as in #168.
        self._conflicts = conflicts
        # Without a proposer a scope correction is recorded and nothing splits.
        self._splits = splits

    async def attribute(
        self,
        rules: Sequence[Rule],
        session_id: UUID,
        assistant_turn: str,
        user_message: str,
        situation: str,
        feedback_id: UUID | None = None,
    ) -> list[Rule] | None:
        """Record what one turn said about the rules in its context.

        Returns the refreshed rules; a failed classification writes nothing and
        returns None, so a queued turn is retried instead of consumed.
        """
        try:
            feedback = await self._classifier.classify(rules, assistant_turn, user_message)
        except Exception:
            logger.warning("Rule attribution failed; nothing written", exc_info=True)
            return None

        refreshed: list[Rule] = []
        for entry in feedback.uses:
            try:
                refreshed.append(
                    await self._service.record_use(
                        entry.rule_id,
                        session_id,
                        situation,
                        entry.outcome,
                        feedback_id=feedback_id,
                    )
                )
            except Exception:
                logger.warning(
                    f"Recording the use of rule {entry.rule_id} failed", exc_info=True
                )

        if self._conflicts is not None and feedback.proposals:
            applied = await self._apply_proposal(
                self._conflicts, feedback.proposals, session_id
            )
            if applied is not None:
                refreshed.append(applied)

        split_refreshed = await self._split_scope_corrections(feedback)
        if split_refreshed:
            # record_use put the pre-split parent in refreshed; replace it
            # with the archived parent the split returned.
            split_ids = {stored.id for stored in split_refreshed}
            refreshed[:] = [
                touched for touched in refreshed if touched.id not in split_ids
            ]
        refreshed.extend(split_refreshed)
        return refreshed or list(rules)

    async def _apply_proposal(
        self, conflicts: ConflictCheck, proposals: list[RuleProposal], session_id: UUID
    ) -> Rule | None:
        """Create the rule this turn proposed, after the conflict check.

        Returns the created rule, or None when the proposal was dropped: more
        than one proposal is a classifier bug, a contradiction is a conflict,
        and a failed check or write costs the proposal, never the uses.
        """
        if len(proposals) > 1:
            logger.warning(
                f"Classifier returned {len(proposals)} proposals; "
                f"acting on the first, dropping {len(proposals) - 1}"
            )
        proposal = proposals[0]
        try:
            active = await self._service.list_active()
            if await conflicts.contradicts(proposal.spec, active):
                logger.info(
                    f"Proposed rule {proposal.spec.condition!r} contradicts the active set"
                )
                return None
            if proposal.origin is RuleOrigin.DICTATED:
                return await self._service.create_dictated(session_id, proposal.spec)
            return await self._service.create_from_correction(proposal.spec)
        except Exception:
            logger.warning(
                f"Applying the proposed rule {proposal.spec.condition!r} failed",
                exc_info=True,
            )
            return None

    async def _split_scope_corrections(self, feedback: Feedback) -> list[Rule]:
        """Split each rule whose scope correction this turn attributed.

        Returns the archived parents and their children, so ``attribute`` can
        replace the pre-split snapshots it recorded. Empty when no proposer is
        configured.
        """
        if self._splits is None:
            return []
        refreshed: list[Rule] = []
        for entry in feedback.uses:
            if entry.outcome is not UseOutcome.SCOPE_CORRECTION:
                continue
            try:
                rule = await self._service.get(entry.rule_id)
                # An archived rule is never applied, so it is never split: its
                # children would inherit ARCHIVED and hold its evidence.
                if rule is None or rule.state is RuleState.ARCHIVED:
                    continue
                uses = await self._service.list_uses(entry.rule_id)
                split_proposal = await self._splits.propose(rule, uses)
                # A use without an id is unassignable; split() rejects it.
                assignments = {
                    use.id: split_proposal.assignment[index]
                    for index, use in enumerate(uses)
                    if use.id is not None
                }
                children = await self._service.split(
                    entry.rule_id, split_proposal.children, assignments
                )
                # The split archived the parent; return it with its children
                # so ``attribute`` can replace the pre-split snapshot.
                archived = await self._service.get(entry.rule_id)
                if archived is not None:
                    refreshed.append(archived)
                refreshed.extend(children)
            except Exception:
                logger.warning(
                    f"Splitting rule {entry.rule_id} failed; the rule and its uses stand",
                    exc_info=True,
                )
        return refreshed
