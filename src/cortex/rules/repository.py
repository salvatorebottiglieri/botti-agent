"""PostgreSQL implementation of RuleRepository."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any
from uuid import UUID

from cortex.db.session import DbSession
from cortex.rules.interfaces import RuleRepository
from cortex.rules.models import Rule, RuleOrigin, RuleState, RuleUse, UseOutcome

_RULE_COLUMNS = (
    "id, condition, action, rationale, state, confidence, origin, "
    "superseded_by, parent_id, created_at, updated_at, archived_at"
)
_USE_COLUMNS = "id, rule_id, session_id, situation, outcome, occurred_at"


class PostgresRuleRepository(RuleRepository):
    """Rule storage over an injectable session factory.

    The factory defaults to the shared pooled ``DbSession``; the CLI passes an
    adapter over its own connection so ``--db-url`` is honoured. Enum columns
    are stored as their string values and read back through the enum, so a
    state or outcome never leaks into the service as a bare ``str``.
    """

    def __init__(
        self, session_factory: Callable[[], AbstractAsyncContextManager[Any]] = DbSession
    ) -> None:
        self._session_factory = session_factory

    async def create(self, rule: Rule) -> Rule:
        async with self._session_factory() as db:
            row = await db.fetchrow(
                f"""
                INSERT INTO rules (id, condition, action, rationale, state,
                                   confidence, origin, superseded_by, parent_id,
                                   created_at, archived_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                RETURNING {_RULE_COLUMNS}
                """,
                rule.id,
                rule.condition,
                rule.action,
                rule.rationale,
                rule.state.value,
                rule.confidence,
                rule.origin.value,
                rule.superseded_by,
                rule.parent_id,
                rule.created_at,
                rule.archived_at,
            )
            return self._row_to_rule(row)

    async def get(self, rule_id: UUID) -> Rule | None:
        async with self._session_factory() as db:
            row = await db.fetchrow(
                f"SELECT {_RULE_COLUMNS} FROM rules WHERE id = $1",
                rule_id,
            )
            if row is None:
                return None
            return self._row_to_rule(row)

    async def add_use(self, use: RuleUse) -> RuleUse:
        async with self._session_factory() as db:
            row = await db.fetchrow(
                f"""
                INSERT INTO rule_uses (rule_id, session_id, situation, outcome,
                                       occurred_at)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING {_USE_COLUMNS}
                """,
                use.rule_id,
                use.session_id,
                use.situation,
                use.outcome.value,
                use.occurred_at,
            )
            return self._row_to_use(row)

    async def list_uses(self, rule_id: UUID) -> list[RuleUse]:
        async with self._session_factory() as db:
            rows = await db.fetch(
                f"""
                SELECT {_USE_COLUMNS}
                FROM rule_uses
                WHERE rule_id = $1
                ORDER BY occurred_at
                """,
                rule_id,
            )
            return [self._row_to_use(row) for row in rows]

    async def list_by_states(
        self, states: Sequence[RuleState], *, limit: int = 500
    ) -> list[Rule]:
        async with self._session_factory() as db:
            rows = await db.fetch(
                f"""
                SELECT {_RULE_COLUMNS}
                FROM rules
                WHERE state = ANY($1)
                ORDER BY confidence DESC
                LIMIT $2
                """,
                [state.value for state in states],
                limit,
            )
            return [self._row_to_rule(row) for row in rows]

    async def list_children(self, rule_id: UUID) -> list[Rule]:
        async with self._session_factory() as db:
            rows = await db.fetch(
                f"""
                SELECT {_RULE_COLUMNS}
                FROM rules
                WHERE superseded_by = $1 OR parent_id = $1
                ORDER BY created_at
                """,
                rule_id,
            )
            return [self._row_to_rule(row) for row in rows]

    async def refresh(self, rule: Rule) -> Rule | None:
        """Rewrite the mutable columns of a rule.

        ``superseded_by`` is written here too: a Split or a Merge archives the
        old rule and points it at its replacement, and this is the only write
        the service has.
        """
        async with self._session_factory() as db:
            row = await db.fetchrow(
                f"""
                UPDATE rules
                SET state = $2, confidence = $3, archived_at = $4,
                    superseded_by = $5, updated_at = NOW()
                WHERE id = $1
                RETURNING {_RULE_COLUMNS}
                """,
                rule.id,
                rule.state.value,
                rule.confidence,
                rule.archived_at,
                rule.superseded_by,
            )
            if row is None:
                return None
            return self._row_to_rule(row)

    async def reassign_uses(self, use_ids: Sequence[int], rule_id: UUID) -> None:
        async with self._session_factory() as db:
            await db.execute(
                "UPDATE rule_uses SET rule_id = $1 WHERE id = ANY($2)",
                rule_id,
                list(use_ids),
            )

    def _row_to_rule(self, row: Any) -> Rule:
        """Convert a database row to a Rule, decoding the enum columns."""
        return Rule(
            id=row["id"],
            condition=row["condition"],
            action=row["action"],
            rationale=row["rationale"],
            state=RuleState(row["state"]),
            confidence=row["confidence"],
            origin=RuleOrigin(row["origin"]),
            superseded_by=row["superseded_by"],
            parent_id=row["parent_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            archived_at=row["archived_at"],
        )

    def _row_to_use(self, row: Any) -> RuleUse:
        """Convert a database row to a RuleUse."""
        return RuleUse(
            id=row["id"],
            rule_id=row["rule_id"],
            session_id=row["session_id"],
            situation=row["situation"],
            outcome=UseOutcome(row["outcome"]),
            occurred_at=row["occurred_at"],
        )
