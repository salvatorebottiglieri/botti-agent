"""Tests for `cortex traces:cleanup` (issue #114 T4).

The command follows the token:create CLI conventions (typer command, nested
async runner via asyncio.run, direct asyncpg connection from --db-url or
settings.database_url) and deletes ONLY ``loop_events`` rows strictly older
than ``now - retention window`` — the same predicate as
TraceRepository.delete_older_than, mirrored on the CLI's own connection so an
explicit --db-url is honored. It is safe on an empty DB and idempotent.

The DB is faked at the asyncpg.connect seam (repo convention: mocked
sessions), and the clock is pinned to FIXED_NOW so the cutoff is
deterministic.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncpg
import pytest
from typer.testing import CliRunner

from cortex.cli import cli

runner = CliRunner()

FIXED_NOW = dt.datetime(2026, 9, 1, 12, 0, 0, tzinfo=dt.UTC)


@pytest.fixture
def pinned_clock(monkeypatch):
    """Pin datetime.now() (resolved at call time by the CLI) to FIXED_NOW."""

    class _FixedClock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return FIXED_NOW

    monkeypatch.setattr(dt, "datetime", _FixedClock)
    return FIXED_NOW


@pytest.fixture
def fake_settings(monkeypatch):
    """Stub get_settings so DB URL + retention come from a controllable source."""
    stub = SimpleNamespace(
        database_url="postgresql://settings:5432/cortex",
        trace_retention_days=45,
    )
    monkeypatch.setattr("cortex.config.loader.get_settings", lambda: stub)
    return stub


@pytest.fixture
def fake_db(monkeypatch):
    """Fake asyncpg.connect returning a recording connection."""
    conn = AsyncMock()
    conn.execute.return_value = "DELETE 0"
    connect = AsyncMock(return_value=conn)
    monkeypatch.setattr(asyncpg, "connect", connect)
    return connect, conn


class TestTracesCleanup:
    def test_deletes_only_rows_older_than_retention_window(self, pinned_clock, fake_settings, fake_db):
        """The single executed statement targets only loop_events with
        created_at < now - retention: rows at/after the cutoff are untouched
        (strict less-than, same semantics as TraceRepository.delete_older_than).
        """
        connect, conn = fake_db
        conn.execute.return_value = "DELETE 3"

        result = runner.invoke(
            cli,
            ["traces:cleanup", "--db-url", "postgresql://flag:5432/cortex", "--days", "30"],
        )

        assert result.exit_code == 0, result.output
        # --db-url flag wins over settings.database_url.
        connect.assert_awaited_once_with("postgresql://flag:5432/cortex")
        # Exactly one statement ran — nothing else (sessions/messages) was touched.
        assert conn.execute.await_count == 1
        sql, cutoff = conn.execute.await_args.args
        assert sql.strip() == "DELETE FROM loop_events WHERE created_at < $1"
        assert cutoff == FIXED_NOW - dt.timedelta(days=30)
        conn.close.assert_awaited_once()
        assert "Deleted 3" in result.output

    def test_db_url_and_retention_default_to_settings(self, pinned_clock, fake_settings, fake_db):
        """No flags: connection URL and retention days come from settings."""
        connect, conn = fake_db
        conn.execute.return_value = "DELETE 2"

        result = runner.invoke(cli, ["traces:cleanup"])

        assert result.exit_code == 0, result.output
        connect.assert_awaited_once_with("postgresql://settings:5432/cortex")
        _, cutoff = conn.execute.await_args.args
        assert cutoff == FIXED_NOW - dt.timedelta(days=45)
        assert "Deleted 2" in result.output

    def test_days_flag_overrides_settings_retention(self, pinned_clock, fake_settings, fake_db):
        """--days narrows the retention window relative to the setting."""
        connect, conn = fake_db
        conn.execute.return_value = "DELETE 1"

        result = runner.invoke(cli, ["traces:cleanup", "--days", "7"])

        assert result.exit_code == 0, result.output
        connect.assert_awaited_once_with("postgresql://settings:5432/cortex")
        _, cutoff = conn.execute.await_args.args
        assert cutoff == FIXED_NOW - dt.timedelta(days=7)

    def test_safe_on_empty_db_and_idempotent(self, pinned_clock, fake_settings, fake_db):
        """DELETE 0 exits 0 and a second run repeats the identical statement."""
        connect, conn = fake_db
        conn.execute.return_value = "DELETE 0"
        args = ["traces:cleanup", "--days", "30"]

        first = runner.invoke(cli, args)
        second = runner.invoke(cli, args)

        assert first.exit_code == 0, first.output
        assert second.exit_code == 0, second.output
        assert "Deleted 0" in first.output
        assert "Deleted 0" in second.output
        assert conn.execute.await_count == 2
        for call in conn.execute.await_args_list:
            assert call.args[0].strip() == "DELETE FROM loop_events WHERE created_at < $1"

    def test_only_loop_events_are_touched(self, pinned_clock, fake_settings, fake_db):
        """Invariant: the statement references loop_events exclusively — never
        the sessions or messages tables."""
        connect, conn = fake_db
        conn.execute.return_value = "DELETE 5"

        result = runner.invoke(cli, ["traces:cleanup", "--days", "30"])

        assert result.exit_code == 0, result.output
        assert conn.execute.await_count == 1
        sql = conn.execute.await_args.args[0]
        assert sql.strip() == "DELETE FROM loop_events WHERE created_at < $1"
        assert "sessions" not in sql
        assert "messages" not in sql
