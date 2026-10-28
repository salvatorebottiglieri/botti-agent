"""Tests for `cortex rules:list` / `rules:show` / `rules:archive` (issue #130).

The commands follow the `traces:cleanup` convention: a nested async runner
under `asyncio.run`, a direct `asyncpg.connect` from `--db-url` (or the
database slice of the settings), and a tiny adapter that lets the ordinary
`PostgresRuleRepository` run over that connection. The DB is faked at the
`asyncpg.connect` seam.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import asyncpg
import pytest
from typer.testing import CliRunner

from cortex.cli import cli
from cortex.config.database import DatabaseSettings
from cortex.rules.models import UseOutcome

runner = CliRunner()

NOW = dt.datetime(2026, 9, 1, 12, 0, 0, tzinfo=dt.UTC)

RULE_ID = UUID("4d2a6f8e-1f3b-4a5c-9d7e-2b8c6a4f0e11")
CHILD_ID = UUID("7e5b3c1a-2d4f-4a6b-8c9d-0e1f2a3b4c5d")
SIBLING_ID = UUID("1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d")
SESSION_ID = UUID("11111111-1111-4111-8111-111111111111")
SUPERSEDED_BY = UUID("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e5f")

CONDITION = "the user asks to book a table"
ACTION = "ask for confirmation before booking"
RATIONALE = "she was charged for a restaurant she did not choose"


def _use_row(outcome: UseOutcome = UseOutcome.APPROVAL, occurred_at: dt.datetime = NOW) -> dict:
    return {
        "id": 1,
        "rule_id": RULE_ID,
        "session_id": SESSION_ID,
        "situation": "book a table for two",
        "outcome": outcome.value,
        "occurred_at": occurred_at,
    }


def _rule_row(
    *,
    rule_id: UUID = RULE_ID,
    condition: str = CONDITION,
    state: str = "definitive",
    # 20/21: one fresh approval on the 0.5 prior, so the sweep recomputes the
    # same number and issues no refresh.
    confidence: float = 20 / 21,
    superseded_by: UUID | None = None,
    parent_id: UUID | None = None,
    archived_at: dt.datetime | None = None,
) -> dict:
    return {
        "id": rule_id,
        "condition": condition,
        "action": ACTION,
        "rationale": RATIONALE,
        "state": state,
        "confidence": confidence,
        "origin": "dictated",
        "superseded_by": superseded_by,
        "parent_id": parent_id,
        "created_at": NOW,
        "updated_at": NOW,
        "archived_at": archived_at,
    }


@pytest.fixture
def fake_settings(monkeypatch):
    """Stub get_settings so the default DB URL is controllable."""
    stub = SimpleNamespace(
        database=DatabaseSettings(
            database_url="postgresql://settings:5432/cortex", _env_file=None
        )
    )
    monkeypatch.setattr("cortex.config.loader.get_settings", lambda: stub)
    return stub


@pytest.fixture
def fake_db(monkeypatch):
    """Fake asyncpg.connect returning a recording connection."""
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value="UPDATE 0")
    connect = AsyncMock(return_value=conn)
    monkeypatch.setattr(asyncpg, "connect", connect)
    return connect, conn


class TestRulesList:
    def test_prints_one_line_per_active_rule(self, fake_settings, fake_db):
        """Witness: a listing that does not round-trip the row prints no rule;
        the confidence is the recomputed posterior, so no refresh is issued."""
        connect, conn = fake_db
        conn.fetch.side_effect = [[_rule_row()], [_use_row()]]

        result = runner.invoke(cli, ["rules:list", "--db-url", "postgresql://flag/cortex"])

        assert result.exit_code == 0, result.output
        connect.assert_awaited_once_with("postgresql://flag/cortex")
        assert str(RULE_ID) in result.output
        assert "definitive" in result.output
        assert CONDITION in result.output
        assert ACTION in result.output
        conn.close.assert_awaited_once()

    def test_no_rules_prints_nothing_to_apply(self, fake_settings, fake_db):
        _, conn = fake_db
        conn.fetch.return_value = []

        result = runner.invoke(cli, ["rules:list"])

        assert result.exit_code == 0, result.output
        assert "no rules" in result.output

    def test_db_url_defaults_to_settings(self, fake_settings, fake_db):
        connect, conn = fake_db
        conn.fetch.return_value = []

        runner.invoke(cli, ["rules:list"])

        connect.assert_awaited_once_with("postgresql://settings:5432/cortex")


class TestRulesShow:
    def test_prints_fields_recent_uses_and_superseded_by(self, fake_settings, fake_db):
        """Witness: a show that never lists the uses hides the evidence the
        score is computed from; a show that hides superseded_by hides the split
        history."""
        _, conn = fake_db
        conn.fetchrow.return_value = _rule_row(
            state="archived", superseded_by=SUPERSEDED_BY, archived_at=NOW
        )
        conn.fetch.side_effect = [[_use_row(UseOutcome.CORRECTION)], []]

        result = runner.invoke(cli, ["rules:show", str(RULE_ID)])

        assert result.exit_code == 0, result.output
        assert str(RULE_ID) in result.output
        assert "archived" in result.output
        assert CONDITION in result.output
        assert ACTION in result.output
        assert RATIONALE in result.output
        assert str(SUPERSEDED_BY) in result.output
        assert "correction" in result.output
        assert "book a table for two" in result.output

    def test_prints_the_children_of_a_split_parent(self, fake_settings, fake_db):
        """Witness: a show that never queries superseded_by cannot print the
        history (splits, merges, replacements) the spec asks for."""
        _, conn = fake_db
        conn.fetchrow.return_value = _rule_row(
            state="archived", superseded_by=CHILD_ID, archived_at=NOW
        )
        conn.fetch.side_effect = [
            [],
            [
                _rule_row(rule_id=CHILD_ID, condition="a dinner booking"),
                _rule_row(rule_id=SIBLING_ID, condition="a pizza order", state="staging"),
            ],
        ]

        result = runner.invoke(cli, ["rules:show", str(RULE_ID)])

        assert result.exit_code == 0, result.output
        replaced = result.output.split("replaced:", 1)[1]
        assert str(CHILD_ID) in replaced
        assert str(SIBLING_ID) in replaced
        assert "a dinner booking" in replaced
        assert "a pizza order" in replaced
        assert "staging" in replaced

    def test_replaced_section_is_empty_for_the_second_child(self, fake_settings, fake_db):
        """Witness: the second child of a split has no incoming edge, so its
        section must be empty rather than inventing history."""
        _, conn = fake_db
        conn.fetchrow.return_value = _rule_row(
            rule_id=CHILD_ID, condition="a pizza order", state="staging", parent_id=RULE_ID
        )
        conn.fetch.side_effect = [[], []]

        result = runner.invoke(cli, ["rules:show", str(CHILD_ID)])

        assert result.exit_code == 0, result.output
        replaced = result.output.split("replaced:", 1)[1]
        assert replaced.strip() == "(none)"

    def test_unknown_rule_exits_non_zero(self, fake_settings, fake_db):
        _, conn = fake_db
        conn.fetchrow.return_value = None

        result = runner.invoke(cli, ["rules:show", str(RULE_ID)])

        assert result.exit_code == 1
        assert "rule not found" in result.output
        assert str(RULE_ID) in result.output

    def test_non_uuid_id_exits_non_zero(self, fake_settings, fake_db):
        """Witness: parsing the id is what makes a typo fail instead of
        querying the database with a non-existent rule."""
        connect, _ = fake_db

        result = runner.invoke(cli, ["rules:show", "not-a-uuid"])

        assert result.exit_code != 0
        connect.assert_not_awaited()


class TestRulesArchive:
    def test_archives_the_rule_and_prints_its_state(self, fake_settings, fake_db):
        """Witness: an archive that reports success without writing the UPDATE
        leaves the rule in the context."""
        _, conn = fake_db
        conn.fetchrow.side_effect = [
            _rule_row(),
            _rule_row(state="archived", archived_at=NOW),
        ]

        result = runner.invoke(cli, ["rules:archive", str(RULE_ID)])

        assert result.exit_code == 0, result.output
        assert str(RULE_ID) in result.output
        assert "archived" in result.output
        update_sql, *params = conn.fetchrow.await_args_list[1].args
        assert "UPDATE rules" in update_sql
        assert params[0] == RULE_ID
        assert params[1] == "archived"

    def test_unknown_rule_exits_non_zero(self, fake_settings, fake_db):
        _, conn = fake_db
        conn.fetchrow.return_value = None

        result = runner.invoke(cli, ["rules:archive", str(RULE_ID)])

        assert result.exit_code == 1
        assert "rule not found" in result.output
