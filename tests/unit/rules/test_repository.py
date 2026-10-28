"""SQL shape and row mapping of PostgresRuleRepository.

The DB is faked at the injected session factory (repo convention: a mocked
session), so these tests pin the statements the service's correctness depends
on: which columns are written, how the enum strings are read back, and that a
missing row is None rather than an exception.
"""

from abc import ABC
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from cortex.db.session import DbSession
from cortex.rules.interfaces import RuleRepository
from cortex.rules.models import Rule, RuleOrigin, RuleState, RuleUse, UseOutcome
from cortex.rules.repository import PostgresRuleRepository

NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)


def _rule_row(rule: Rule) -> dict:
    return {
        "id": rule.id,
        "condition": rule.condition,
        "action": rule.action,
        "rationale": rule.rationale,
        "state": rule.state.value,
        "confidence": rule.confidence,
        "origin": rule.origin.value,
        "superseded_by": rule.superseded_by,
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
        "archived_at": rule.archived_at,
    }


def _use_row(use: RuleUse) -> dict:
    return {
        "id": 7,
        "rule_id": use.rule_id,
        "session_id": use.session_id,
        "situation": use.situation,
        "outcome": use.outcome.value,
        "occurred_at": use.occurred_at,
    }


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
def repo(mock_session):
    factory = MagicMock(return_value=mock_session)
    return PostgresRuleRepository(session_factory=factory), factory, mock_session


class TestInterface:
    def test_repository_is_an_abc(self):
        assert issubclass(RuleRepository, ABC)
        with pytest.raises(TypeError, match="abstract"):
            RuleRepository()  # type: ignore[abstract]

    def test_postgres_impl_is_a_concrete_subclass(self):
        assert issubclass(PostgresRuleRepository, RuleRepository)
        PostgresRuleRepository()

    def test_default_session_factory_is_the_shared_db_session(self):
        repo = PostgresRuleRepository()

        assert repo._session_factory is DbSession


class TestCreate:
    async def test_inserts_the_rule_and_maps_the_returning_row(self, repo):
        """Witness: reading `state` raw leaves a str where RuleState is expected,
        which breaks every comparison downstream."""
        repository, factory, session = repo
        rule = Rule(
            condition="book a table",
            action="confirm first",
            rationale="she was charged by mistake",
            state=RuleState.DEFINITIVE,
            confidence=20 / 21,
            origin=RuleOrigin.DICTATED,
        )
        session.fetchrow.return_value = _rule_row(rule)

        created = await repository.create(rule)

        factory.assert_called_once()
        sql = session.fetchrow.call_args.args[0]
        assert "INSERT INTO rules" in sql
        assert "RETURNING" in sql
        params = session.fetchrow.call_args.args[1:]
        assert params[0] == rule.id
        assert params[4] == "definitive"
        assert params[6] == "dictated"
        assert created.state is RuleState.DEFINITIVE
        assert created.origin is RuleOrigin.DICTATED
        assert created.confidence == pytest.approx(20 / 21)


class TestGet:
    async def test_selects_by_id_and_maps_the_row(self, repo):
        repository, _, session = repo
        rule = Rule(
            condition="c",
            action="a",
            state=RuleState.ARCHIVED,
            superseded_by=uuid4(),
            archived_at=NOW,
        )
        session.fetchrow.return_value = _rule_row(rule)

        found = await repository.get(rule.id)

        sql = session.fetchrow.call_args.args[0]
        assert "SELECT" in sql and "FROM rules" in sql and "WHERE id = $1" in sql
        assert session.fetchrow.call_args.args[1] == rule.id
        assert found is not None
        assert found.state is RuleState.ARCHIVED
        assert found.superseded_by == rule.superseded_by
        assert found.archived_at == NOW

    async def test_missing_rule_is_none(self, repo):
        repository, _, session = repo
        session.fetchrow.return_value = None

        assert await repository.get(uuid4()) is None


class TestAddUse:
    async def test_inserts_the_use_and_returns_its_generated_id(self, repo):
        repository, _, session = repo
        use = RuleUse(
            rule_id=uuid4(),
            session_id=uuid4(),
            situation="book a table",
            outcome=UseOutcome.IMPLICIT,
            occurred_at=NOW,
        )
        session.fetchrow.return_value = _use_row(use)

        stored = await repository.add_use(use)

        sql = session.fetchrow.call_args.args[0]
        assert "INSERT INTO rule_uses" in sql
        assert "RETURNING" in sql
        params = session.fetchrow.call_args.args[1:]
        assert params == (use.rule_id, use.session_id, "book a table", "implicit", NOW)
        assert stored.id == 7
        assert stored.outcome is UseOutcome.IMPLICIT


class TestListUses:
    async def test_selects_a_rules_uses_oldest_first(self, repo):
        repository, _, session = repo
        rule_id = uuid4()
        use = RuleUse(
            rule_id=rule_id,
            session_id=uuid4(),
            situation="book",
            outcome=UseOutcome.CORRECTION,
            occurred_at=NOW,
        )
        session.fetch.return_value = [_use_row(use)]

        uses = await repository.list_uses(rule_id)

        sql = session.fetch.call_args.args[0]
        assert "FROM rule_uses" in sql
        assert "WHERE rule_id = $1" in sql
        assert "ORDER BY occurred_at" in sql
        assert session.fetch.call_args.args[1] == rule_id
        assert len(uses) == 1
        assert uses[0].outcome is UseOutcome.CORRECTION
        assert uses[0].id == 7


class TestListByStates:
    async def test_filters_by_state_orders_by_confidence_and_limits(self, repo):
        """Witness: passing the RuleState members instead of their string values
        sends an enum representation PostgreSQL cannot compare to VARCHAR."""
        repository, _, session = repo
        rule = Rule(condition="c", action="a", state=RuleState.DEFINITIVE, confidence=0.9)
        session.fetch.return_value = [_rule_row(rule)]

        rules = await repository.list_by_states(
            [RuleState.STAGING, RuleState.DEFINITIVE], limit=7
        )

        sql = session.fetch.call_args.args[0]
        assert "FROM rules" in sql
        assert "state = ANY($1)" in sql
        assert "ORDER BY confidence DESC" in sql
        assert "LIMIT $2" in sql
        assert session.fetch.call_args.args[1] == ["staging", "definitive"]
        assert session.fetch.call_args.args[2] == 7
        assert [r.state for r in rules] == [RuleState.DEFINITIVE]

    async def test_empty_result_is_an_empty_list(self, repo):
        repository, _, session = repo
        session.fetch.return_value = []

        assert await repository.list_by_states([RuleState.STAGING]) == []


class TestRefresh:
    async def test_rewrites_state_confidence_archive_and_supersede_columns(self, repo):
        """Witness: a refresh that forgets `superseded_by` leaves a split parent
        superseded by nobody, and the history of the split is lost."""
        repository, _, session = repo
        superseded_by = uuid4()
        rule = Rule(
            condition="c",
            action="a",
            state=RuleState.ARCHIVED,
            confidence=0.12,
            superseded_by=superseded_by,
            archived_at=NOW,
        )
        session.fetchrow.return_value = _rule_row(rule)

        refreshed = await repository.refresh(rule)

        sql = session.fetchrow.call_args.args[0]
        assert "UPDATE rules" in sql
        assert "state = $2" in sql
        assert "confidence = $3" in sql
        assert "superseded_by = $5" in sql
        assert "RETURNING" in sql
        assert session.fetchrow.call_args.args[1:] == (
            rule.id,
            "archived",
            0.12,
            NOW,
            superseded_by,
        )
        assert refreshed is not None
        assert refreshed.state is RuleState.ARCHIVED

    async def test_missing_row_is_none(self, repo):
        """Witness: a row deleted concurrently must be reported, not invented."""
        repository, _, session = repo
        session.fetchrow.return_value = None

        assert await repository.refresh(Rule()) is None


class TestReassignUses:
    async def test_updates_the_rule_id_of_the_given_uses(self, repo):
        repository, _, session = repo
        target = uuid4()
        session.execute.return_value = "UPDATE 2"

        reassigned = await repository.reassign_uses([3, 4], target)

        sql = session.execute.call_args.args[0]
        assert "UPDATE rule_uses SET rule_id = $1" in sql
        assert "WHERE id = ANY($2)" in sql
        assert session.execute.call_args.args[1:] == (target, [3, 4])
        assert reassigned == 2

    async def test_no_ids_writes_nothing(self, repo):
        repository, _, session = repo

        assert await repository.reassign_uses([], uuid4()) == 0
        session.execute.assert_not_called()

