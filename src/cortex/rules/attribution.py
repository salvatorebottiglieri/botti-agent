"""AttributionRun — a turn's Feedback becomes uses on the rules it applied (#168)
and, with a ``ConflictCheck``, the rule it proposed (#170).

The run is the only writer of a turn's evidence. A classifier or storage
failure must never reach the caller: the turn already happened, and the worst
case is one turn of evidence lost, not a broken reply. A proposal is the
cheaper thing to lose than the uses already written.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from uuid import UUID

from cortex.rules.conflicts import ConflictCheck
from cortex.rules.feedback import FeedbackClassifier
from cortex.rules.models import Rule, RuleOrigin
from cortex.rules.service import RuleService

logger = logging.getLogger(__name__)


class AttributionRun:
    """Classifies one turn and records its uses, and its proposal, through the
    ``RuleService``."""

    def __init__(
        self,
        classifier: FeedbackClassifier,
        service: RuleService,
        conflicts: ConflictCheck | None = None,
    ) -> None:
        self._classifier = classifier
        self._service = service
        # Without a check the proposals are ignored, exactly as in #168.
        self._conflicts = conflicts

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
            if len(feedback.proposals) > 1:
                logger.warning(
                    f"Classifier returned {len(feedback.proposals)} proposals; "
                    f"acting on the first, dropping {len(feedback.proposals) - 1}"
                )
            proposal = feedback.proposals[0]
            try:
                active = await self._service.list_active()
                if await self._conflicts.contradicts(proposal.spec, active):
                    logger.info(
                        f"Proposed rule {proposal.spec.condition!r} contradicts the active set"
                    )
                elif proposal.origin is RuleOrigin.DICTATED:
                    refreshed.append(
                        await self._service.create_dictated(session_id, proposal.spec)
                    )
                else:
                    refreshed.append(await self._service.create_from_correction(proposal.spec))
            except Exception:
                logger.warning(
                    f"Applying the proposed rule {proposal.spec.condition!r} failed",
                    exc_info=True,
                )
        return refreshed or list(rules)
