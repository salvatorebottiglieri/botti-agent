"""QueuedFeedback — turns waiting for attribution (docs/specs/rule-system.md #169).

A turn that had rules in context and got a reply is written as a pending row
and attributed by a later pass, so a provider outage or a restart costs a
delay, never evidence. The drain is a plain loop: the partial unique index on
``rule_uses (feedback_id, rule_id)`` is what makes a repeated pass harmless.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from cortex.db.session import DbSession
from cortex.rules.attribution import AttributionRun
from cortex.rules.constants import VALIDITY_WINDOW
from cortex.rules.interfaces import FeedbackQueue
from cortex.rules.models import Rule
from cortex.rules.service import RuleService

logger = logging.getLogger(__name__)

_QUEUE_COLUMNS = (
    "id, session_id, rule_ids, assistant_turn, user_reply, status, "
    "occurred_at, attributed_at"
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class QueuedFeedbackStatus(StrEnum):
    """Where a queued turn sits: waiting, attributed, or too old to be."""

    PENDING = "pending"
    ATTRIBUTED = "attributed"
    DROPPED = "dropped"


@dataclass(frozen=True)
class QueuedFeedback:
    """One turn waiting for attribution, with the bundle it could see."""

    session_id: UUID
    rule_ids: tuple[UUID, ...]
    assistant_turn: str
    user_reply: str
    status: QueuedFeedbackStatus = QueuedFeedbackStatus.PENDING
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    attributed_at: datetime | None = None
    id: UUID | None = None


class PostgresFeedbackQueue(FeedbackQueue):
    """Queue storage over an injectable session factory, like the repository."""

    def __init__(
        self, session_factory: Callable[[], AbstractAsyncContextManager[Any]] = DbSession
    ) -> None:
        self._session_factory = session_factory

    async def enqueue(self, item: QueuedFeedback) -> QueuedFeedback:
        # The SQL CHECK is the database's copy of this rule; this guard is the
        # testable one.
        if not item.user_reply.strip():
            raise ValueError("a turn with no user reply is never enqueued")
        async with self._session_factory() as db:
            row = await db.fetchrow(
                f"""
                INSERT INTO queued_feedback (session_id, rule_ids, assistant_turn,
                                             user_reply, status, occurred_at,
                                             attributed_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING {_QUEUE_COLUMNS}
                """,
                item.session_id,
                list(item.rule_ids),
                item.assistant_turn,
                item.user_reply,
                item.status.value,
                item.occurred_at,
                item.attributed_at,
            )
            return self._row_to_item(row)

    async def list_pending(self, limit: int = 10) -> list[QueuedFeedback]:
        async with self._session_factory() as db:
            rows = await db.fetch(
                f"""
                SELECT {_QUEUE_COLUMNS}
                FROM queued_feedback
                WHERE status = $1
                ORDER BY occurred_at
                LIMIT $2
                """,
                QueuedFeedbackStatus.PENDING.value,
                limit,
            )
            return [self._row_to_item(row) for row in rows]

    async def mark_attributed(self, feedback_id: UUID, now: datetime) -> None:
        async with self._session_factory() as db:
            await db.execute(
                """
                UPDATE queued_feedback
                SET status = $2, attributed_at = $3
                WHERE id = $1 AND status = 'pending'
                """,
                feedback_id,
                QueuedFeedbackStatus.ATTRIBUTED.value,
                now,
            )

    async def drop_stale(self, cutoff: datetime) -> int:
        async with self._session_factory() as db:
            rows = await db.fetch(
                """
                UPDATE queued_feedback
                SET status = $2
                WHERE status = 'pending' AND occurred_at < $1
                RETURNING id
                """,
                cutoff,
                QueuedFeedbackStatus.DROPPED.value,
            )
            return len(rows)

    def _row_to_item(self, row: Any) -> QueuedFeedback:
        """Convert a database row to a QueuedFeedback, decoding the enum column."""
        return QueuedFeedback(
            id=row["id"],
            session_id=row["session_id"],
            rule_ids=tuple(row["rule_ids"]),
            assistant_turn=row["assistant_turn"],
            user_reply=row["user_reply"],
            status=QueuedFeedbackStatus(row["status"]),
            occurred_at=row["occurred_at"],
            attributed_at=row["attributed_at"],
        )


class FeedbackDrain:
    """Attributes the pending rows, one pass at a time."""

    def __init__(
        self,
        queue: FeedbackQueue,
        run: AttributionRun,
        service: RuleService,
        *,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._queue = queue
        self._run = run
        self._service = service
        self._clock = clock

    async def drain(self, limit: int = 10) -> int:
        """Attribute one pass over the pending rows; returns how many it took.

        A repository failure is logged and the pass still reports the
        attributions it already made; a turn whose classification failed stays
        pending, so a later pass retries it.
        """
        attributed = 0
        try:
            for item in await self._queue.list_pending(limit):
                if item.id is None:
                    continue
                rules: list[Rule] = []
                for rule_id in item.rule_ids:
                    rule = await self._service.get(rule_id)
                    if rule is not None:
                        rules.append(rule)
                refreshed = await self._run.attribute(
                    rules,
                    item.session_id,
                    item.assistant_turn,
                    item.user_reply,
                    situation=item.user_reply,
                    feedback_id=item.id,
                )
                if refreshed is None:
                    continue
                # Marked even when the turn said nothing about its rules: a
                # second pass has nothing more to say.
                await self._queue.mark_attributed(item.id, self._clock())
                attributed += 1
            await self._queue.drop_stale(self._clock() - VALIDITY_WINDOW)
        except Exception:
            logger.warning("The queued-feedback drain failed", exc_info=True)
        return attributed


async def enqueue_turn(
    queue: FeedbackQueue,
    rules: Sequence[Rule],
    session_id: UUID,
    assistant_turn: str,
    user_reply: str,
) -> QueuedFeedback | None:
    """Queue one turn for attribution; None when there is nothing to attribute.

    The rules are the turn's own bundle, so attribution can never name a rule
    the model could not see. Never raises: the turn already happened, so a lost
    row costs one turn of evidence, never a reply.
    """
    if not rules or not user_reply.strip():
        return None
    try:
        return await queue.enqueue(
            QueuedFeedback(
                session_id=session_id,
                rule_ids=tuple(rule.id for rule in rules),
                assistant_turn=assistant_turn,
                user_reply=user_reply,
            )
        )
    except Exception:
        logger.warning("Queueing the turn for attribution failed", exc_info=True)
        return None
