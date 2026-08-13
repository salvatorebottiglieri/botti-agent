"""Goal repository implementations.

The Goal model uses epoch-float timestamps; the `goals` table stores
TIMESTAMPTZ. PostgresGoalRepository converts between the two. `deadline`
has no column and is persisted inside the `metadata` JSONB under the
"deadline" key. The `ended_at` column exists but the Goal model has no
field for it — it is left NULL.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from cortex.agentic.models import Goal, GoalStatus
from cortex.db.session import DbSession
from cortex.goals.interfaces import GoalRepository

_ACTIVE_STATUSES = (GoalStatus.PENDING, GoalStatus.RUNNING, GoalStatus.PAUSED)


def _to_db_timestamp(ts: float | datetime | None) -> datetime | None:
    """Convert an epoch-float timestamp to an aware UTC datetime (TIMESTAMPTZ).

    Epoch floats are rounded to microsecond precision so writes are
    deterministic (TIMESTAMPTZ stores microseconds). Naive datetimes are
    normalized to UTC; asyncpg rejects naive datetimes for TIMESTAMPTZ.
    """
    if ts is None:
        return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return ts
    return datetime.fromtimestamp(round(ts, 6), tz=UTC)


def _deadline_metadata(goal: Goal) -> dict[str, Any]:
    """Persist deadline inside metadata JSONB (no deadline column).

    Always returns a {"deadline": ...} entry; when the model has no deadline
    the value is an explicit None tombstone, so a later update overwrites
    (never resurrects) a deadline persisted earlier. Naive datetimes are
    interpreted as UTC so the epoch float roundtrips to the same wall-clock
    time on any host. Float deadlines are epoch timestamps (the Goal model's
    convention for every other timestamp field), converted exactly like
    _to_db_timestamp before the epoch is stored.
    """
    deadline = goal.deadline
    if isinstance(deadline, datetime):
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        return {"deadline": deadline.timestamp()}
    if isinstance(deadline, float):
        # Mirror _to_db_timestamp: epoch floats are rounded to microsecond
        # precision, then stored as the corresponding aware UTC epoch.
        return {"deadline": datetime.fromtimestamp(round(deadline, 6), tz=UTC).timestamp()}
    return {"deadline": None}


class PostgresGoalRepository(GoalRepository):
    """
    PostgreSQL implementation of goal storage.

    Uses the shared db session for connection pooling.
    """

    _COLUMNS = (
        "id, description, priority, status, created_at, started_at, "
        "completed_at, error, metadata"
    )

    async def create(self, goal: Goal) -> Goal:
        """Insert a goal and return it."""
        async with DbSession() as db:
            row = await db.fetchrow(
                f"""
                INSERT INTO goals
                    (id, description, priority, status, created_at,
                     started_at, completed_at, error, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING {self._COLUMNS}
                """,
                goal.id,
                goal.description,
                goal.priority,
                goal.status.value,
                _to_db_timestamp(goal.created_at),
                _to_db_timestamp(goal.started_at),
                _to_db_timestamp(goal.completed_at),
                goal.error,
                _deadline_metadata(goal),
            )
            return self._row_to_goal(row)

    async def get(self, goal_id: UUID) -> Goal | None:
        """Get a goal by ID."""
        async with DbSession() as db:
            row = await db.fetchrow(
                f"""
                SELECT {self._COLUMNS}
                FROM goals
                WHERE id = $1
                """,
                goal_id,
            )
            if row is None:
                return None
            return self._row_to_goal(row)

    async def update(self, goal: Goal) -> None:
        """Persist changes to an existing goal."""
        async with DbSession() as db:
            await db.execute(
                """
                UPDATE goals
                SET description = $1, priority = $2, status = $3,
                    started_at = $4, completed_at = $5, error = $6,
                    metadata = COALESCE(metadata, '{}'::jsonb) || $7
                WHERE id = $8
                """,
                goal.description,
                goal.priority,
                goal.status.value,
                _to_db_timestamp(goal.started_at),
                _to_db_timestamp(goal.completed_at),
                goal.error,
                _deadline_metadata(goal),
                goal.id,
            )

    async def list_active(self, limit: int | None = None) -> list[Goal]:
        """List active goals (pending, running, or paused), newest first.

        `limit` bounds the number of rows; None (the default) means no limit.
        """
        sql = f"""
            SELECT {self._COLUMNS}
            FROM goals
            WHERE status = ANY($1)
            ORDER BY created_at DESC
        """
        params: list[Any] = [[s.value for s in _ACTIVE_STATUSES]]
        if limit is not None:
            sql += "\nLIMIT $2"
            params.append(limit)
        async with DbSession() as db:
            rows = await db.fetch(sql, *params)
            return [self._row_to_goal(row) for row in rows]

    async def get_in_flight(self) -> list[Goal]:
        """List goals left running at shutdown, oldest first."""
        async with DbSession() as db:
            rows = await db.fetch(
                f"""
                SELECT {self._COLUMNS}
                FROM goals
                WHERE status = ANY($1)
                ORDER BY created_at
                """,
                [GoalStatus.RUNNING.value],
            )
            return [self._row_to_goal(row) for row in rows]

    def _row_to_goal(self, row: Any) -> Goal:
        """Convert a database row to a Goal model."""
        metadata = row["metadata"] or {}
        deadline_raw = metadata.get("deadline")
        deadline = (
            datetime.fromtimestamp(deadline_raw, tz=UTC)
            if deadline_raw is not None
            else None
        )
        return Goal(
            id=row["id"],
            description=row["description"],
            priority=row["priority"],
            status=GoalStatus(row["status"]),
            created_at=row["created_at"].timestamp() if row["created_at"] else None,
            started_at=row["started_at"].timestamp() if row["started_at"] else None,
            completed_at=row["completed_at"].timestamp() if row["completed_at"] else None,
            error=row["error"],
            deadline=deadline,
        )


class InMemoryGoalRepository(GoalRepository):
    """
    In-memory goal storage; the ExecutionModule default for tests.

    Stores and returns the same Goal object instances so callers may
    mutate the held object and observe the changes through the repo.
    """

    def __init__(self) -> None:
        self._goals: dict[UUID, Goal] = {}

    async def create(self, goal: Goal) -> Goal:
        """Store the goal and return the same object."""
        self._goals[goal.id] = goal
        return goal

    async def get(self, goal_id: UUID) -> Goal | None:
        """Get the stored goal object by ID."""
        return self._goals.get(goal_id)

    async def update(self, goal: Goal) -> None:
        """Replace the stored goal with the updated object.

        A goal that was never created is a no-op, mirroring the Postgres
        UPDATE affecting zero rows (no implicit insert).
        """
        if goal.id not in self._goals:
            return
        self._goals[goal.id] = goal

    async def list_active(self, limit: int | None = None) -> list[Goal]:
        """List active goals (pending, running, or paused), newest first.

        `limit` bounds the number of rows; None (the default) means no limit.
        """
        active = [g for g in self._goals.values() if g.status in _ACTIVE_STATUSES]
        active.sort(key=lambda g: g.created_at or 0.0, reverse=True)
        if limit is None:
            return active
        return active[:limit]

    async def get_in_flight(self) -> list[Goal]:
        """List goals left running at shutdown, oldest first."""
        in_flight = [g for g in self._goals.values() if g.status == GoalStatus.RUNNING]
        in_flight.sort(key=lambda g: g.created_at or 0.0)
        return in_flight
