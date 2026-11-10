"""Tests for `cortex memory:backfill-embeddings` (ADR-0008, issue #159).

The command follows the `rules:list` convention: a nested async runner under
``asyncio.run``, a direct ``asyncpg.connect`` from ``--db-url`` (or the
database slice of the settings), and the ordinary ``PostgresFactRepository``
over that connection. The DB is faked at the ``asyncpg.connect`` seam and the
LLM at the factory; the runner is replaced to make the wiring and the output
deterministic.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncpg
import pytest
from typer.testing import CliRunner

from cortex.cli import cli
from cortex.config.database import DatabaseSettings
from cortex.memory.backfill import BackfillStats
from cortex.memory.repository import PostgresFactRepository

runner = CliRunner()

LLM_CLIENT = object()


class FakeLLMClientFactory:
    """Records the settings slice the command builds the client from."""

    created_with: object | None = None

    def __init__(self, llm: object) -> None:
        FakeLLMClientFactory.created_with = llm

    def create(self) -> object:
        return LLM_CLIENT


class FakeRunner:
    """Stands in for EmbeddingBackfillRunner, recording its wiring."""

    stats = BackfillStats()
    instances: list[FakeRunner] = []

    def __init__(
        self,
        fact_repository: object,
        llm_client: object,
        *,
        batch_size: int = 100,
    ) -> None:
        self.fact_repository = fact_repository
        self.llm_client = llm_client
        self.batch_size = batch_size
        FakeRunner.instances.append(self)

    async def run(self) -> BackfillStats:
        return FakeRunner.stats


@pytest.fixture
def fake_settings(monkeypatch):
    """Stub get_settings so the default DB URL and LLM slice are controllable."""
    stub = SimpleNamespace(
        database=DatabaseSettings(
            database_url="postgresql://settings:5432/cortex", _env_file=None
        ),
        llm=object(),
    )
    monkeypatch.setattr("cortex.config.loader.get_settings", lambda: stub)
    return stub


@pytest.fixture
def fake_db(monkeypatch):
    """Fake asyncpg.connect returning a recording connection."""
    conn = AsyncMock()
    conn.execute.return_value = "REINDEX"
    connect = AsyncMock(return_value=conn)
    monkeypatch.setattr(asyncpg, "connect", connect)
    return connect, conn


@pytest.fixture
def fake_runner(monkeypatch):
    """Replace the runner and the LLM factory at their module seams."""
    monkeypatch.setattr("cortex.memory.backfill.EmbeddingBackfillRunner", FakeRunner)
    monkeypatch.setattr("cortex.llm.factory.LLMClientFactory", FakeLLMClientFactory)
    FakeRunner.instances = []
    FakeRunner.stats = BackfillStats()
    FakeLLMClientFactory.created_with = None
    return FakeRunner


class TestBackfillEmbeddingsCommand:
    def test_reports_counts_and_reminds_the_operator_to_reindex(
        self, fake_settings, fake_db, fake_runner
    ):
        """Witness: a run that reports success without the REINDEX reminder
        leaves a stale ivfflat index the operator was never told to rebuild."""
        connect, conn = fake_db
        fake_runner.stats = BackfillStats(scanned=5, updated=4, failed=1)

        result = runner.invoke(cli, ["memory:backfill-embeddings"])

        assert result.exit_code == 0, result.output
        connect.assert_awaited_once_with("postgresql://settings:5432/cortex")
        assert "scanned 5, updated 4, failed 1" in result.output
        assert (
            "run REINDEX INDEX CONCURRENTLY idx_facts_embedding after a large backfill"
            in result.output
        )
        assert "reindexed idx_facts_embedding" not in result.output
        conn.execute.assert_not_awaited()
        conn.close.assert_awaited_once()

    def test_wires_batch_size_llm_client_and_repository(self, fake_settings, fake_db, fake_runner):
        """Witness: building a fresh client or dropping the batch size makes
        the command ignore the operator's flags and the settings slice."""
        _, _ = fake_db

        result = runner.invoke(cli, ["memory:backfill-embeddings", "--batch-size", "7"])

        assert result.exit_code == 0, result.output
        assert len(fake_runner.instances) == 1
        instance = fake_runner.instances[0]
        assert instance.batch_size == 7
        assert instance.llm_client is LLM_CLIENT
        assert isinstance(instance.fact_repository, PostgresFactRepository)
        assert FakeLLMClientFactory.created_with is fake_settings.llm

    def test_db_url_flag_wins_over_settings(self, fake_settings, fake_db, fake_runner):
        """Witness: ignoring --db-url runs the backfill against the wrong DB."""
        connect, _ = fake_db

        result = runner.invoke(
            cli, ["memory:backfill-embeddings", "--db-url", "postgresql://flag/cortex"]
        )

        assert result.exit_code == 0, result.output
        connect.assert_awaited_once_with("postgresql://flag/cortex")

    def test_reindex_flag_rebuilds_the_index_after_a_write(self, fake_settings, fake_db, fake_runner):
        """Witness: --reindex that never calls REINDEX leaves the stale index."""
        _, conn = fake_db
        fake_runner.stats = BackfillStats(scanned=2, updated=2, failed=0)

        result = runner.invoke(cli, ["memory:backfill-embeddings", "--reindex"])

        assert result.exit_code == 0, result.output
        conn.execute.assert_awaited_once_with(
            "REINDEX INDEX CONCURRENTLY idx_facts_embedding"
        )
        assert "reindexed idx_facts_embedding" in result.output
        assert "run REINDEX" not in result.output

    def test_reindex_runs_even_when_nothing_was_updated(self, fake_settings, fake_db, fake_runner):
        """Witness: an earlier aborted run may have left the index stale, so
        the explicit flag is unconditional once the backfill completes."""
        _, conn = fake_db
        fake_runner.stats = BackfillStats(scanned=3, updated=0, failed=3)

        result = runner.invoke(cli, ["memory:backfill-embeddings", "--reindex"])

        assert result.exit_code == 0, result.output
        conn.execute.assert_awaited_once_with(
            "REINDEX INDEX CONCURRENTLY idx_facts_embedding"
        )
        assert "reindexed idx_facts_embedding" in result.output
        assert "scanned 3, updated 0, failed 3" in result.output

    def test_no_updated_rows_prints_no_reindex_reminder(self, fake_settings, fake_db, fake_runner):
        _, _ = fake_db
        fake_runner.stats = BackfillStats(scanned=0, updated=0, failed=0)

        result = runner.invoke(cli, ["memory:backfill-embeddings"])

        assert result.exit_code == 0, result.output
        assert "scanned 0, updated 0, failed 0" in result.output
        assert "REINDEX" not in result.output

    def test_batch_size_below_one_is_rejected(self, fake_settings, fake_db, fake_runner):
        connect, _ = fake_db

        result = runner.invoke(cli, ["memory:backfill-embeddings", "--batch-size", "0"])

        assert result.exit_code != 0
        connect.assert_not_awaited()
        assert not fake_runner.instances

    def test_llm_construction_error_exits_non_zero(self, fake_settings, fake_db, fake_runner, monkeypatch):
        """Witness: a provider misconfiguration must fail the command, not
        silently report a clean backfill."""
        connect, conn = fake_db

        def broken_factory(llm: object) -> object:
            raise RuntimeError("unsupported provider")

        monkeypatch.setattr("cortex.llm.factory.LLMClientFactory", broken_factory)

        result = runner.invoke(cli, ["memory:backfill-embeddings"])

        assert result.exit_code != 0
        connect.assert_awaited_once()
        conn.close.assert_awaited_once()
