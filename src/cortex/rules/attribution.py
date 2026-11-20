"""AttributionRun — a turn's Feedback becomes uses on the rules it applied (#168).

The run is the only writer of a turn's evidence. A classifier or storage
failure must never reach the caller: the turn already happened, and the worst
case is one turn of evidence lost, not a broken reply.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from uuid import UUID

from cortex.rules.feedback import FeedbackClassifier
from cortex.rules.models import Rule
from cortex.rules.service import RuleService

logger = logging.getLogger(__name__)


class AttributionRun:
    """Classifies one turn and records its uses through the ``RuleService``."""

    def __init__(self, classifier: FeedbackClassifier, service: RuleService) -> None:
        self._classifier = classifier
        self._service = service

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

        # #170 owns the proposals: a proposal becomes a staging rule there.
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
        return refreshed or list(rules)
