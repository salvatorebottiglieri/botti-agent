"""QueuedFeedback tests (#169): the statements and the drain.

The database is faked at the injected session factory (repo convention: a
mocked session) so the statements the drain's correctness depends on are
pinned; the drain itself runs against the in-memory doubles in ``fakes.py``.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from cortex.db.session import DbSession
from cortex.rules.attribution import AttributionRun
from cortex.rules.constants import VALIDITY_WINDOW
from cortex.rules.feedback import AttributedUse, Feedback
from cortex.rules.models import Rule, RuleSpec, RuleUse, UseOutcome
from cortex.rules.queue import (
    FeedbackDrain,
    PostgresFeedbackQueue,
    QueuedFeedback,
    QueuedFeedbackStatus,
    enqueue_turn,
)
from cortex.rules.repository import PostgresRuleRepository
from cortex.rules.service import RuleService
from tests.unit.rules.fakes import (
    InMemoryFeedbackQueue,
    InMemoryRuleRepository,
    RaisingQueue,
)

ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = ROOT / "migrations" / "011_queued_feedback.sql"

NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
SESSION = UUID("11111111-1111-4111-8111-111111111111")
SPEC = RuleSpec(condition="the user asks to book a table", action="ask before booking")


def _item(**overrides) -> QueuedFeedback:
    fields = {
        "session_id": SESSION,
        "rule_ids": (uuid4(),),
        "assistant_turn": "of course, booking now",
        "user_reply": "yes thanks",
        "occurred_at": NOW,
    }
    fields.update(overrides)
    return QueuedFeedback(**fields)


def _item_row(item: QueuedFeedback) -> dict:
    return {
        "id": item.id,
        "session_id": item.session_id,
        "rule_ids": list(item.rule_ids),
        "assistant_turn": item.assistant_turn,
        "user_reply": item.user_reply,
        "status": item.status.value,
        "occurred_at": item.occurred_at,
        "attributed_at": item.attributed_at,
    }


class StubClassifier:
    """A classifier that returns what the test hands it, or raises."""

    def __init__(self, feedback: Feedback | None = None, error: Exception | None = None) -> None:
        self._feedback = feedback or Feedback()
        self._error = error
        self.seen: list[list] = []

    async def classify(self, rules, assistant_turn, user_message) -> Feedback:
        if self._error is not None:
            raise self._error
        self.seen.append(list(rules))
        return self._feedback


def _run_returning_none() -> MagicMock:
    """An attribution run whose classification failed: it returns None."""
    run = MagicMock(spec=AttributionRun)
    run.attribute = AsyncMock(return_value=None)
    return run


class SpyQueue(InMemoryFeedbackQueue):
    """An in-memory queue that records the cutoffs its staleness sweep got and
    how many enqueue attempts reached it."""

    def __init__(self) -> None:
        super().__init__()
        self.cutoffs: list[datetime] = []
        self.enqueue_calls = 0

    async def enqueue(self, item: QueuedFeedback) -> QueuedFeedback:
        self.enqueue_calls += 1
        return await super().enqueue(item)

    async def drop_stale(self, cutoff: datetime) -> int:
        self.cutoffs.append(cutoff)
        return await super().drop_stale(cutoff)


@pytest.fixture
def mock_session():
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.fetchrow = AsyncMock()
    session.fetch = AsyncMock()
    session.execute = AsyncMock()
    return session


@pytest.fixture
def queued(mock_session):
    factory = MagicMock(return_value=mock_session)
    return PostgresFeedbackQueue(session_factory=factory), factory, mock_session


def _service(repository: InMemoryRuleRepository) -> RuleService:
    return RuleService(repository, clock=lambda: NOW)


class TestInterface:
    def test_the_default_session_factory_is_the_shared_db_session(self):
        assert PostgresFeedbackQueue()._session_factory is DbSession


class TestQueuedFeedbackMigration:
    """Migration 011 is the schema the queue and the double-scoring guard need."""

    @pytest.mark.parametrize(
        "snippet",
        [
            "CREATE TABLE IF NOT EXISTS queued_feedback",
            "user_reply TEXT NOT NULL CHECK (length(btrim(user_reply)) > 0)",
            "status VARCHAR(20) NOT NULL DEFAULT 'pending'",
            "ALTER TABLE rule_uses ADD COLUMN IF NOT EXISTS feedback_id UUID",
            "REFERENCES queued_feedback(id) ON DELETE SET NULL",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_rule_uses_feedback_rule",
            "ON rule_uses(feedback_id, rule_id) WHERE feedback_id IS NOT NULL",
        ],
    )
    def test_migration_contains_the_queue_schema(self, snippet: str) -> None:
        assert snippet in MIGRATION_PATH.read_text()

    def test_every_statement_is_guarded(self) -> None:
        """Witness: an unguarded statement breaks every re-run of the migrations."""
        body = "\n".join(
            line
            for line in MIGRATION_PATH.read_text().splitlines()
            if not line.lstrip().startswith("--")
        )
        statements = [part.strip() for part in body.split(";") if part.strip()]

        assert len(statements) == 4
        for statement in statements:
            assert "IF NOT EXISTS" in statement, statement


class TestPostgresStatements:
    async def test_enqueue_inserts_every_column_of_the_row(self, queued):
        """Witness: dropping `rule_ids` from the INSERT leaves a row nobody can
        attribute — the turn's own bundle is the only bound on attribution."""
        queue, factory, session = queued
        item = _item(id=uuid4(), occurred_at=NOW)
        session.fetchrow.return_value = _item_row(item)

        stored = await queue.enqueue(item)

        factory.assert_called_once()
        sql = session.fetchrow.call_args.args[0]
        assert "INSERT INTO queued_feedback" in sql
        for column in (
            "session_id",
            "rule_ids",
            "assistant_turn",
            "user_reply",
            "status",
            "occurred_at",
            "attributed_at",
        ):
            assert column in sql
        assert "RETURNING" in sql
        assert session.fetchrow.call_args.args[1:] == (
            item.session_id,
            list(item.rule_ids),
            item.assistant_turn,
            item.user_reply,
            "pending",
            item.occurred_at,
            None,
        )
        assert stored.id == item.id
        assert stored.status is QueuedFeedbackStatus.PENDING

    async def test_enqueue_refuses_a_blank_reply_before_the_database(self, queued):
        queue, factory, _ = queued

        with pytest.raises(ValueError):
            await queue.enqueue(_item(user_reply="   "))

        factory.assert_not_called()

    async def test_list_pending_filters_on_status_orders_and_limits(self, queued):
        """Witness: an unfiltered SELECT re-attributes rows that are already done."""
        queue, _, session = queued
        item = _item(id=uuid4())
        session.fetch.return_value = [_item_row(item)]

        pending = await queue.list_pending(limit=5)

        sql = session.fetch.call_args.args[0]
        assert "FROM queued_feedback" in sql
        assert "WHERE status = $1" in sql
        assert "ORDER BY occurred_at" in sql
        assert "LIMIT $2" in sql
        assert session.fetch.call_args.args[1:] == ("pending", 5)
        assert [row.id for row in pending] == [item.id]

    async def test_mark_attributed_updates_a_pending_row_only(self, queued):
        queue, _, session = queued
        feedback_id = uuid4()

        assert await queue.mark_attributed(feedback_id, NOW) is None

        sql = session.execute.call_args.args[0]
        assert "UPDATE queued_feedback" in sql
        assert "SET status = $2" in sql
        assert "attributed_at = $3" in sql
        assert "WHERE id = $1" in sql
        assert "status = 'pending'" in sql
        assert session.execute.call_args.args[1:] == (feedback_id, "attributed", NOW)

    async def test_drop_stale_is_one_update_that_returns_the_count(self, queued):
        queue, _, session = queued
        cutoff = NOW - VALIDITY_WINDOW
        session.fetch.return_value = [{"id": uuid4()}, {"id": uuid4()}]

        dropped = await queue.drop_stale(cutoff)

        assert session.fetch.await_count == 1
        sql = session.fetch.call_args.args[0]
        assert "UPDATE queued_feedback" in sql
        assert "SET status = $2" in sql
        assert "status = 'pending'" in sql
        assert "occurred_at < $1" in sql
        assert "RETURNING id" in sql
        assert session.fetch.call_args.args[1:] == (cutoff, "dropped")
        assert dropped == 2

    async def test_a_rule_use_write_carries_its_feedback_id(self, mock_session):
        """Witness: without the column on the INSERT the partial unique index
        that prevents double scoring can never fire."""
        repository = PostgresRuleRepository(
            session_factory=MagicMock(return_value=mock_session)
        )
        feedback_id = uuid4()
        rule_id = uuid4()
        session = mock_session
        session.fetchrow.return_value = {
            "id": 7,
            "rule_id": rule_id,
            "session_id": SESSION,
            "situation": "book a table",
            "outcome": "implicit",
            "occurred_at": NOW,
            "feedback_id": feedback_id,
        }

        stored = await repository.add_use(
            RuleUse(
                rule_id=rule_id,
                session_id=SESSION,
                situation="book a table",
                outcome=UseOutcome.IMPLICIT,
                occurred_at=NOW,
                feedback_id=feedback_id,
            )
        )

        sql = session.fetchrow.call_args.args[0]
        assert "INSERT INTO rule_uses" in sql
        assert "feedback_id" in sql
        assert session.fetchrow.call_args.args[-1] == feedback_id
        assert stored.feedback_id == feedback_id


class TestInMemoryQueue:
    async def test_a_blank_reply_is_refused(self):
        queue = InMemoryFeedbackQueue()

        with pytest.raises(ValueError):
            await queue.enqueue(_item(user_reply="  \n "))

        assert queue.items == []

    async def test_pending_rows_come_back_oldest_first_and_limited(self):
        queue = InMemoryFeedbackQueue()
        late = await queue.enqueue(_item(occurred_at=NOW + timedelta(minutes=5)))
        early = await queue.enqueue(_item(occurred_at=NOW - timedelta(minutes=5)))

        assert [row.id for row in await queue.list_pending()] == [early.id, late.id]
        assert [row.id for row in await queue.list_pending(limit=1)] == [early.id]

    async def test_mark_attributed_removes_the_row_from_pending(self):
        queue = InMemoryFeedbackQueue()
        stored = await queue.enqueue(_item())

        await queue.mark_attributed(stored.id, NOW)

        assert await queue.list_pending() == []
        assert queue.items[0].status is QueuedFeedbackStatus.ATTRIBUTED
        assert queue.items[0].attributed_at == NOW

    async def test_marking_an_attributed_row_again_is_a_no_op(self):
        queue = InMemoryFeedbackQueue()
        stored = await queue.enqueue(_item())

        await queue.mark_attributed(stored.id, NOW)
        await queue.mark_attributed(stored.id, NOW + timedelta(hours=1))

        assert queue.items[0].attributed_at == NOW

    async def test_drop_stale_drops_only_the_rows_past_the_cutoff(self):
        queue = InMemoryFeedbackQueue()
        old = await queue.enqueue(_item(occurred_at=NOW - timedelta(days=200)))
        fresh = await queue.enqueue(_item(occurred_at=NOW))

        dropped = await queue.drop_stale(NOW - VALIDITY_WINDOW)

        assert dropped == 1
        assert [row.id for row in queue.items] == [old.id, fresh.id]
        assert queue.items[0].status is QueuedFeedbackStatus.DROPPED
        assert queue.items[1].status is QueuedFeedbackStatus.PENDING


class TestDrain:
    async def test_the_drain_attributes_a_pending_row(self):
        """Witness: a capped `situation` loses the reply text a Split later
        judges use membership by."""
        repository = InMemoryRuleRepository()
        service = _service(repository)
        rule = await service.create_from_correction(SPEC)
        queue = SpyQueue()
        reply = "y" * 250
        stored = await queue.enqueue(_item(rule_ids=(rule.id,), user_reply=reply))
        classifier = StubClassifier(
            Feedback(uses=[AttributedUse(rule_id=rule.id, outcome=UseOutcome.APPROVAL)])
        )
        drain = FeedbackDrain(queue, AttributionRun(classifier, service), service, clock=lambda: NOW)

        assert await drain.drain() == 1

        assert [
            (use.rule_id, use.session_id, use.situation, use.outcome, use.feedback_id)
            for use in repository.uses
        ] == [(rule.id, SESSION, reply, UseOutcome.APPROVAL, stored.id)]
        assert queue.items[0].status is QueuedFeedbackStatus.ATTRIBUTED
        assert queue.items[0].attributed_at == NOW
        assert queue.cutoffs == [NOW - VALIDITY_WINDOW]

    async def test_the_drain_marks_a_row_attributed_when_nothing_was_attributed(self):
        repository = InMemoryRuleRepository()
        service = _service(repository)
        rule = await service.create_from_correction(SPEC)
        queue = InMemoryFeedbackQueue()
        await queue.enqueue(_item(rule_ids=(rule.id,)))
        drain = FeedbackDrain(
            queue, AttributionRun(StubClassifier(Feedback()), service), service, clock=lambda: NOW
        )

        assert await drain.drain() == 1

        assert queue.items[0].status is QueuedFeedbackStatus.ATTRIBUTED
        assert repository.uses == []

    async def test_the_drain_skips_a_rule_id_that_no_longer_exists(self):
        repository = InMemoryRuleRepository()
        service = _service(repository)
        rule = await service.create_from_correction(SPEC)
        queue = InMemoryFeedbackQueue()
        await queue.enqueue(_item(rule_ids=(uuid4(), rule.id)))
        classifier = StubClassifier(
            Feedback(uses=[AttributedUse(rule_id=rule.id, outcome=UseOutcome.APPROVAL)])
        )
        drain = FeedbackDrain(queue, AttributionRun(classifier, service), service, clock=lambda: NOW)

        assert await drain.drain() == 1

        assert [[seen.id for seen in call] for call in classifier.seen] == [[rule.id]]
        assert [use.rule_id for use in repository.uses] == [rule.id]
        assert queue.items[0].status is QueuedFeedbackStatus.ATTRIBUTED

    async def test_a_failed_row_does_not_stop_the_pass(self):
        repository = InMemoryRuleRepository()
        service = _service(repository)
        rule = await service.create_from_correction(SPEC)
        queue = InMemoryFeedbackQueue()
        first_session = uuid4()
        await queue.enqueue(
            _item(
                session_id=first_session,
                rule_ids=(rule.id,),
                occurred_at=NOW - timedelta(minutes=1),
            )
        )
        later = await queue.enqueue(_item(rule_ids=(rule.id,), occurred_at=NOW))
        run = MagicMock(spec=AttributionRun)
        run.attribute = AsyncMock(side_effect=[None, [rule]])
        drain = FeedbackDrain(queue, run, service, clock=lambda: NOW)

        assert await drain.drain() == 1

        assert [call.args[1] for call in run.attribute.call_args_list] == [
            first_session,
            later.session_id,
        ]
        assert [row.status for row in queue.items] == [
            QueuedFeedbackStatus.PENDING,
            QueuedFeedbackStatus.ATTRIBUTED,
        ]

    async def test_the_drain_leaves_a_row_pending_when_the_classification_failed(self):
        """Witness: consuming the row after a failed classification loses the
        turn's evidence for good."""
        repository = InMemoryRuleRepository()
        service = _service(repository)
        rule = await service.create_from_correction(SPEC)
        queue = InMemoryFeedbackQueue()
        await queue.enqueue(_item(rule_ids=(rule.id,)))
        drain = FeedbackDrain(queue, _run_returning_none(), service, clock=lambda: NOW)

        assert await drain.drain() == 0

        assert queue.items[0].status is QueuedFeedbackStatus.PENDING
        assert repository.uses == []

        classifier = StubClassifier(
            Feedback(uses=[AttributedUse(rule_id=rule.id, outcome=UseOutcome.APPROVAL)])
        )
        retry = FeedbackDrain(
            queue, AttributionRun(classifier, service), service, clock=lambda: NOW
        )

        assert await retry.drain() == 1

        assert [use.rule_id for use in repository.uses] == [rule.id]
        assert queue.items[0].status is QueuedFeedbackStatus.ATTRIBUTED

    async def test_a_queue_failure_is_logged_and_the_pass_still_reports(self, caplog):
        repository = InMemoryRuleRepository()
        service = _service(repository)
        rule = await service.create_from_correction(SPEC)
        queue = InMemoryFeedbackQueue()
        await queue.enqueue(_item(rule_ids=(rule.id,)))
        queue.drop_stale = AsyncMock(side_effect=RuntimeError("connection lost"))
        classifier = StubClassifier(
            Feedback(uses=[AttributedUse(rule_id=rule.id, outcome=UseOutcome.APPROVAL)])
        )
        drain = FeedbackDrain(queue, AttributionRun(classifier, service), service, clock=lambda: NOW)

        with caplog.at_level(logging.WARNING):
            assert await drain.drain() == 1

        assert [use.rule_id for use in repository.uses] == [rule.id]
        assert "connection lost" in caplog.text

    async def test_re_running_the_drain_writes_no_second_use(self, caplog):
        """The double-scoring guard: one turn scores a rule at most once.

        The row is put back to pending, so the second pass really classifies it
        and really writes: the (feedback_id, rule_id) index, not the queue's
        bookkeeping, is what stops the second use.
        """
        repository = InMemoryRuleRepository()
        service = _service(repository)
        rule = await service.create_from_correction(SPEC)
        queue = InMemoryFeedbackQueue()
        stored = await queue.enqueue(_item(rule_ids=(rule.id,)))
        classifier = StubClassifier(
            Feedback(uses=[AttributedUse(rule_id=rule.id, outcome=UseOutcome.APPROVAL)])
        )
        drain = FeedbackDrain(queue, AttributionRun(classifier, service), service, clock=lambda: NOW)

        assert await drain.drain() == 1
        queue.items[0] = replace(
            queue.items[0], status=QueuedFeedbackStatus.PENDING, attributed_at=None
        )
        with caplog.at_level(logging.WARNING):
            assert await drain.drain() == 1

        assert len(classifier.seen) == 2
        assert [use.rule_id for use in repository.uses] == [rule.id]
        assert repository.uses[0].feedback_id == stored.id
        assert "already has a use from feedback" in caplog.text


class TestEnqueueTurn:
    """``enqueue_turn``: one pending row, or none, and never an exception."""

    async def test_a_turn_with_rules_and_a_reply_writes_one_pending_row(self):
        queue = InMemoryFeedbackQueue()
        first = Rule(condition="when x", action="do y")
        second = Rule(condition="when a", action="do b")

        row = await enqueue_turn(
            queue, [first, second], SESSION, "of course, doing it", "yes thanks"
        )

        assert row is not None
        assert row.id is not None
        assert queue.items == [row]
        assert row.session_id == SESSION
        assert row.rule_ids == (first.id, second.id)
        assert row.assistant_turn == "of course, doing it"
        assert row.user_reply == "yes thanks"
        assert row.status is QueuedFeedbackStatus.PENDING

    async def test_a_turn_with_no_rules_writes_nothing(self):
        queue = InMemoryFeedbackQueue()

        row = await enqueue_turn(queue, [], SESSION, "of course", "yes thanks")

        assert row is None
        assert queue.items == []

    @pytest.mark.parametrize("reply", ["", "   ", "\n\t "])
    async def test_a_blank_reply_writes_nothing(self, reply: str):
        queue = SpyQueue()

        row = await enqueue_turn(
            queue, [Rule(condition="when x", action="do y")], SESSION, "of course", reply
        )

        assert row is None
        assert queue.enqueue_calls == 0
        assert queue.items == []

    async def test_a_store_failure_is_swallowed_and_logged(self, caplog):
        queue = RaisingQueue()
        rule = Rule(condition="when x", action="do y")

        with caplog.at_level(logging.WARNING, logger="cortex.rules.queue"):
            row = await enqueue_turn(queue, [rule], SESSION, "of course", "yes thanks")

        assert row is None
        assert "connection lost" in caplog.text
