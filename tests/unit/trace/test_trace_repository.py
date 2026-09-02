"""Tests for the trace repository (interface + Postgres impl) — issue #111 T1.

The ``loop_events`` persistence contract under test:

* insert_event persists (session_id, seq, event_type, payload) — payload is
  the event's self-describing to_dict() JSON and is treated as opaque: it
  must round-trip verbatim, the repository has no field knowledge.
* list_events returns a session's events ordered by seq (oldest first).
* delete_older_than removes only rows strictly older than the cutoff; rows at
  or newer than the cutoff survive the statement.
"""

from abc import ABC
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from cortex.trace.interfaces import TraceRepository
from cortex.trace.models import TraceEvent
from cortex.trace.repository import PostgresTraceRepository


class TestTraceRepositoryInterface:
    """Verify the trace interface mirrors the SessionRepository style."""

    def test_repository_is_abc(self):
        """TraceRepository is abstract and cannot be instantiated directly."""
        assert issubclass(TraceRepository, ABC)
        with pytest.raises(TypeError, match="abstract"):
            TraceRepository()

    def test_repository_has_required_methods(self):
        """All required persistence primitives are declared."""
        for method in ("insert_event", "list_events", "delete_older_than"):
            assert hasattr(TraceRepository, method)
            assert callable(getattr(TraceRepository, method))

    def test_postgres_impl_is_concrete_subclass(self):
        """PostgresTraceRepository implements the full interface."""
        assert issubclass(PostgresTraceRepository, TraceRepository)
        PostgresTraceRepository()  # must instantiate


class TestPostgresTraceRepository:
    """Test cases for PostgresTraceRepository using a mocked DbSession."""

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
        with patch("cortex.trace.repository.DbSession") as mock_db_session_class:
            mock_db_session_class.return_value = mock_db_session
            yield PostgresTraceRepository(), mock_db_session

    @pytest.mark.asyncio
    async def test_insert_event_roundtrips_payload_verbatim(self, repo_with_mock):
        """insert_event persists the primitive inputs and returns the stored row.

        Payload is opaque: a nested dict (a loop event's self-describing
        to_dict() shape) must come back unchanged — no field knowledge.
        """
        repo, mock_db_session = repo_with_mock
        session_id = uuid4()
        now = datetime.now(UTC)
        payload = {
            "event_type": "tool_done",
            "session_id": str(session_id),
            "tool_name": "shell",
            "tool_call_id": "call_123",
            "success": False,
            "meta": {"nested": [1, 2, {"flag": True}]},
        }
        mock_db_session.fetchrow.return_value = {
            "id": 7,
            "session_id": session_id,
            "seq": 3,
            "event_type": "tool_done",
            "payload": payload,
            "created_at": now,
        }

        event = await repo.insert_event(
            session_id=session_id, seq=3, event_type="tool_done", payload=payload
        )

        assert isinstance(event, TraceEvent)
        assert event.id == 7
        assert event.session_id == session_id
        assert event.seq == 3
        assert event.event_type == "tool_done"
        assert event.payload == payload
        assert event.created_at == now
        sql = mock_db_session.fetchrow.call_args.args[0]
        assert "INSERT INTO loop_events" in sql
        assert "RETURNING" in sql
        # The bound parameters are exactly the primitive inputs, payload untouched.
        assert mock_db_session.fetchrow.call_args.args[1:] == (
            session_id,
            3,
            "tool_done",
            payload,
        )

    @pytest.mark.asyncio
    async def test_list_events_orders_by_seq_and_maps_verbatim(self, repo_with_mock):
        """list_events returns one session's rows oldest-first, verbatim."""
        repo, mock_db_session = repo_with_mock
        session_id = uuid4()
        now = datetime.now(UTC)
        payloads = [
            {"event_type": "thinking", "message": "first"},
            {"event_type": "tool_done", "success": True},
            {"event_type": "done", "delta": "bye"},
        ]
        # DB ORDER BY seq would yield this exact order.
        mock_db_session.fetch.return_value = [
            {
                "id": 1,
                "session_id": session_id,
                "seq": 0,
                "event_type": "thinking",
                "payload": payloads[0],
                "created_at": now,
            },
            {
                "id": 2,
                "session_id": session_id,
                "seq": 1,
                "event_type": "tool_done",
                "payload": payloads[1],
                "created_at": now,
            },
            {
                "id": 3,
                "session_id": session_id,
                "seq": 2,
                "event_type": "done",
                "payload": payloads[2],
                "created_at": now,
            },
        ]

        events = await repo.list_events(session_id)

        assert [e.seq for e in events] == [0, 1, 2]
        assert [e.event_type for e in events] == ["thinking", "tool_done", "done"]
        assert [e.payload for e in events] == payloads
        sql = mock_db_session.fetch.call_args.args[0]
        assert "FROM loop_events" in sql
        assert "ORDER BY seq" in sql
        # Only the requested session is bound.
        assert mock_db_session.fetch.call_args.args[1] == session_id

    @pytest.mark.asyncio
    async def test_list_events_empty_session(self, repo_with_mock):
        """A session with no events lists as an empty sequence."""
        repo, mock_db_session = repo_with_mock
        mock_db_session.fetch.return_value = []

        events = await repo.list_events(uuid4())

        assert events == []

    @pytest.mark.asyncio
    async def test_delete_older_than_removes_only_rows_below_cutoff(
        self, repo_with_mock
    ):
        """delete_older_than deletes strictly-older rows and reports the count.

        The predicate is created_at < cutoff: rows at or newer than the cutoff
        are never touched by the statement.
        """
        repo, mock_db_session = repo_with_mock
        cutoff = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
        mock_db_session.execute.return_value = "DELETE 2"

        deleted = await repo.delete_older_than(cutoff)

        assert deleted == 2
        sql = mock_db_session.execute.call_args.args[0]
        assert sql.strip() == "DELETE FROM loop_events WHERE created_at < $1"
        # The cutoff is the only bound parameter, strict-less-than semantics.
        assert mock_db_session.execute.call_args.args[1:] == (cutoff,)

    @pytest.mark.asyncio
    async def test_delete_older_than_nothing_matches_returns_zero(
        self, repo_with_mock
    ):
        """DELETE affecting zero rows parses to a zero count."""
        repo, mock_db_session = repo_with_mock
        mock_db_session.execute.return_value = "DELETE 0"

        deleted = await repo.delete_older_than(datetime.now(UTC))

        assert deleted == 0
