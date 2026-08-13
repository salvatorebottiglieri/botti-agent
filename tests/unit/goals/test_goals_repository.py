"""Tests for PostgresGoalRepository using a mocked DbSession.

Invariant under test (System Architecture):
    A goal's mapped fields survive a create -> read roundtrip (id,
    description, priority, status, timestamps, error, deadline). The
    negation -- a persisted field differs after get() -- must never hold.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from cortex.agentic.models import Goal, GoalStatus
from cortex.goals.repository import PostgresGoalRepository


class TestPostgresGoalRepository:
    """Test cases for PostgresGoalRepository."""

    @pytest.fixture
    def mock_db_session(self):
        """Create a mock DbSession context manager."""
        mock = MagicMock()
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=None)
        mock.fetchrow = AsyncMock()
        mock.fetch = AsyncMock()
        mock.execute = AsyncMock()
        return mock

    @pytest.fixture
    def repo_with_mock(self, mock_db_session):
        """Create a repository with mocked DB."""
        with patch("cortex.goals.repository.DbSession") as mock_db_session_class:
            mock_db_session_class.return_value = mock_db_session
            yield PostgresGoalRepository(), mock_db_session

    @pytest.mark.asyncio
    async def test_create_goal_roundtrip_preserves_mapped_fields(self, repo_with_mock):
        """All mapped fields survive create -> get (invariant: no field differs).

        created_at/started_at use sub-microsecond epoch floats (as produced by
        time.time()): TIMESTAMPTZ stores microseconds, so the persisted value
        differs from the model value by <1us. The timestamp fields are asserted
        with approx; everything else is exact. Exact equality on the timestamps
        would be red for these values (the test must not pass by value
        selection).
        """
        repo, mock_db_session = repo_with_mock
        goal_id = uuid4()
        created_at = 1779273000.1234567  # sub-microsecond precision
        started_at = 1779273000.1234567
        deadline = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
        # What Postgres returns: TIMESTAMPTZ stores microseconds.
        stored_created = datetime.fromtimestamp(round(created_at, 6), tz=UTC)
        stored_started = datetime.fromtimestamp(round(started_at, 6), tz=UTC)
        row = {
            "id": goal_id,
            "description": "Write the quarterly report",
            "priority": "high",
            "status": "running",
            "created_at": stored_created,
            "started_at": stored_started,
            "completed_at": None,
            "ended_at": None,
            "error": None,
            "metadata": {"deadline": deadline.timestamp()},
        }
        mock_db_session.fetchrow.return_value = row

        goal = Goal(
            id=goal_id,
            description="Write the quarterly report",
            priority="high",
            status=GoalStatus.RUNNING,
            created_at=created_at,
            started_at=started_at,
            completed_at=None,
            deadline=deadline,
            error=None,
        )

        created = await repo.create(goal)
        fetched = await repo.get(goal_id)

        assert created.id == fetched.id == goal_id
        assert fetched.description == goal.description
        assert fetched.priority == goal.priority
        assert fetched.status == goal.status
        # The DB stores microseconds: the sub-us model value roundtrips to
        # within 1us, never exactly. Exact equality here is a value-selection
        # accident; a real >1us regression fails even with the tolerance.
        assert fetched.created_at == pytest.approx(goal.created_at, abs=1e-6)
        assert fetched.started_at == pytest.approx(goal.started_at, abs=1e-6)
        assert fetched.completed_at == goal.completed_at
        assert fetched.error == goal.error
        assert fetched.deadline == goal.deadline
        # The write is microsecond-deterministic: the epoch float is rounded
        # to 6 decimals before conversion (stable/idempotent across writes).
        create_call = mock_db_session.fetchrow.call_args_list[0]
        assert create_call.args[5] == datetime.fromtimestamp(round(created_at, 6), tz=UTC)
        assert create_call.args[6] == datetime.fromtimestamp(round(started_at, 6), tz=UTC)

    @pytest.mark.asyncio
    async def test_create_writes_epoch_floats_as_utc_datetimes(self, repo_with_mock):
        """The mapper converts epoch-float timestamps to aware TIMESTAMPTZ values."""
        repo, mock_db_session = repo_with_mock
        mock_db_session.fetchrow.return_value = {
            "id": uuid4(),
            "description": "x",
            "priority": "normal",
            "status": "pending",
            "created_at": datetime.now(UTC),
            "started_at": None,
            "completed_at": None,
            "ended_at": None,
            "error": None,
            "metadata": {},
        }

        await repo.create(Goal(description="x", created_at=1000.5))

        args, _ = mock_db_session.fetchrow.call_args
        # args[0] is SQL; args[1:] are parameters (id, description, priority, status, created_at, ...)
        created_at_arg = args[5]
        assert isinstance(created_at_arg, datetime)
        assert created_at_arg.tzinfo is not None
        assert created_at_arg == datetime.fromtimestamp(1000.5, tz=UTC)

    @pytest.mark.asyncio
    async def test_deadline_stored_in_metadata_and_read_back(self, repo_with_mock):
        """deadline has no column; it roundtrips through the metadata JSONB."""
        repo, mock_db_session = repo_with_mock
        deadline = datetime(2026, 7, 4, 18, 0, 0, tzinfo=UTC)
        row = {
            "id": uuid4(),
            "description": "x",
            "priority": "normal",
            "status": "pending",
            "created_at": datetime.now(UTC),
            "started_at": None,
            "completed_at": None,
            "ended_at": None,
            "error": None,
            "metadata": {"deadline": deadline.timestamp(), "source": "api"},
        }
        mock_db_session.fetchrow.return_value = row

        goal = Goal(description="x", deadline=deadline)
        await repo.create(goal)
        fetched = await repo.get(goal.id)

        # On write the epoch float must be inside the metadata payload.
        create_call = mock_db_session.fetchrow.call_args_list[0]
        metadata_arg = create_call.args[9]
        assert metadata_arg["deadline"] == deadline.timestamp()
        # On read the epoch float comes back as an aware UTC datetime.
        assert fetched.deadline == deadline

    @pytest.mark.asyncio
    async def test_get_returns_none_for_missing_goal(self, repo_with_mock):
        repo, mock_db_session = repo_with_mock
        mock_db_session.fetchrow.return_value = None

        result = await repo.get(uuid4())

        assert result is None

    @pytest.mark.asyncio
    async def test_update_persists_status_transition(self, repo_with_mock):
        """update writes the full mapped state for a status transition."""
        repo, mock_db_session = repo_with_mock
        goal = Goal(
            description="Doomed",
            status=GoalStatus.FAILED,
            error="boom",
            created_at=1000.0,
            started_at=1001.0,
            completed_at=1002.0,
        )

        await repo.update(goal)

        args, _ = mock_db_session.execute.call_args
        # args = (sql, description, priority, status, started_at, completed_at, error, metadata, id)
        assert args[3] == "failed"
        assert args[4] == datetime.fromtimestamp(1001.0, tz=UTC)
        assert args[5] == datetime.fromtimestamp(1002.0, tz=UTC)
        assert args[6] == "boom"
        assert args[8] == goal.id

    @pytest.mark.asyncio
    async def test_list_active_returns_active_goals(self, repo_with_mock):
        """list_active filters to pending/running/paused and applies the limit."""
        repo, mock_db_session = repo_with_mock
        now = datetime.now(UTC)
        mock_db_session.fetch.return_value = [
            {
                "id": uuid4(),
                "description": "active",
                "priority": "normal",
                "status": "pending",
                "created_at": now,
                "started_at": None,
                "completed_at": None,
                "ended_at": None,
                "error": None,
                "metadata": {},
            },
        ]

        result = await repo.list_active(limit=5)

        assert len(result) == 1
        assert result[0].status == GoalStatus.PENDING
        # The active-status set is bound as a parameter, not inlined in SQL.
        assert mock_db_session.fetch.call_args.args[1] == ["pending", "running", "paused"]
        # The limit is bound after the status list.
        assert mock_db_session.fetch.call_args.args[2] == 5

    @pytest.mark.asyncio
    async def test_get_in_flight_returns_only_running(self, repo_with_mock):
        """get_in_flight selects exactly the status = 'running' rows."""
        repo, mock_db_session = repo_with_mock
        now = datetime.now(UTC)
        running_id = uuid4()
        mock_db_session.fetch.return_value = [
            {
                "id": running_id,
                "description": "in flight",
                "priority": "normal",
                "status": "running",
                "created_at": now,
                "started_at": now,
                "completed_at": None,
                "ended_at": None,
                "error": None,
                "metadata": {},
            },
        ]

        result = await repo.get_in_flight()

        assert len(result) == 1
        assert result[0].id == running_id
        assert result[0].status == GoalStatus.RUNNING
        # The status filter is bound as a parameter, not inlined in SQL.
        assert mock_db_session.fetch.call_args.args[1] == ["running"]

    @pytest.mark.asyncio
    async def test_list_active_no_limit_returns_every_row(self, repo_with_mock):
        """limit=None returns all active rows and emits no LIMIT clause."""
        repo, mock_db_session = repo_with_mock
        now = datetime.now(UTC)
        mock_db_session.fetch.return_value = [
            {
                "id": uuid4(),
                "description": f"active-{i}",
                "priority": "normal",
                "status": "pending",
                "created_at": now,
                "started_at": None,
                "completed_at": None,
                "ended_at": None,
                "error": None,
                "metadata": {},
            }
            for i in range(12)
        ]

        result = await repo.list_active(limit=None)

        assert len(result) == 12
        sql = mock_db_session.fetch.call_args.args[0]
        assert "LIMIT" not in sql

    @pytest.mark.asyncio
    async def test_update_merges_metadata_keeping_existing_keys(self, repo_with_mock):
        """UPDATE merges the deadline dict into the stored JSONB (||) so
        pre-existing metadata keys survive; $7 is still the fresh deadline dict."""
        repo, mock_db_session = repo_with_mock
        deadline = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
        goal = Goal(description="Merge me", deadline=deadline, created_at=1000.0)

        await repo.update(goal)

        args, _ = mock_db_session.execute.call_args
        sql = args[0]
        assert "||" in sql
        assert args[7] == {"deadline": deadline.timestamp()}

    @pytest.mark.asyncio
    async def test_update_with_none_deadline_clears_stored_deadline(self, repo_with_mock):
        """An update with deadline=None tombstones a previously stored deadline.

        The JSONB || merge would leave the old "deadline" key untouched, so the
        update payload must carry an explicit {"deadline": None}: $7 is exactly
        that, and a row read back with the null tombstone maps to deadline=None
        (never resurrects the stale deadline).
        """
        repo, mock_db_session = repo_with_mock
        goal = Goal(description="Clear me", deadline=None, created_at=1000.0)

        await repo.update(goal)

        args, _ = mock_db_session.execute.call_args
        assert args[7] == {"deadline": None}

        # Read-after-update: the merged metadata holds the explicit null
        # tombstone, which _row_to_goal must map back to deadline=None.
        row = {
            "id": goal.id,
            "description": "Clear me",
            "priority": "normal",
            "status": "pending",
            "created_at": datetime.fromtimestamp(1000.0, tz=UTC),
            "started_at": None,
            "completed_at": None,
            "ended_at": None,
            "error": None,
            "metadata": {"deadline": None},
        }
        mock_db_session.fetchrow.return_value = row
        fetched = await repo.get(goal.id)
        assert fetched.deadline is None

    @pytest.mark.asyncio
    async def test_naive_deadline_roundtrips_as_utc(self, repo_with_mock):
        """A naive deadline is interpreted as UTC on write, so create -> get
        is identity (no host-local timezone shift on the epoch float)."""
        repo, mock_db_session = repo_with_mock
        naive = datetime(2026, 6, 1, 12, 0, 0)  # no tzinfo
        as_utc = naive.replace(tzinfo=UTC)
        row = {
            "id": uuid4(),
            "description": "x",
            "priority": "normal",
            "status": "pending",
            "created_at": datetime.now(UTC),
            "started_at": None,
            "completed_at": None,
            "ended_at": None,
            "error": None,
            "metadata": {"deadline": as_utc.timestamp()},
        }
        mock_db_session.fetchrow.return_value = row

        goal = Goal(description="x", deadline=naive)
        await repo.create(goal)
        fetched = await repo.get(goal.id)

        # The write must store the UTC-interpreted epoch, not the host-local one.
        create_call = mock_db_session.fetchrow.call_args_list[0]
        metadata_arg = create_call.args[9]
        assert metadata_arg["deadline"] == as_utc.timestamp()
        # ... so the read-back is identity (no timezone shift).
        assert fetched.deadline == as_utc

    @pytest.mark.asyncio
    async def test_float_deadline_treated_as_epoch_timestamp(self, repo_with_mock):
        """A float deadline is an epoch timestamp (the Goal model's convention
        for every other timestamp field), not silently dropped: it persists as
        the aware UTC epoch and reads back as the aware UTC datetime."""
        repo, mock_db_session = repo_with_mock
        deadline_epoch = 1780300800.0
        row = {
            "id": uuid4(),
            "description": "x",
            "priority": "normal",
            "status": "pending",
            "created_at": datetime.now(UTC),
            "started_at": None,
            "completed_at": None,
            "ended_at": None,
            "error": None,
            "metadata": {"deadline": deadline_epoch},
        }
        mock_db_session.fetchrow.return_value = row

        goal = Goal(description="x", deadline=deadline_epoch)
        await repo.create(goal)
        fetched = await repo.get(goal.id)

        # On write the epoch float is preserved in the metadata payload.
        create_call = mock_db_session.fetchrow.call_args_list[0]
        metadata_arg = create_call.args[9]
        assert metadata_arg["deadline"] == deadline_epoch
        # On read it comes back as the corresponding aware UTC datetime.
        assert fetched.deadline == datetime.fromtimestamp(deadline_epoch, tz=UTC)
