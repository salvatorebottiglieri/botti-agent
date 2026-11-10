"""Tests for the evidence engine (issue #83).

Invariants under test:
    * Migration 010 creates the evidence table, its foreign key and both
      indexes.
    * ``update_rule`` is the documented logit-space Bayesian update and
      saturates at 0/1.
    * ``PostgresEvidenceStore.record`` deduplicates at the one-hour
      inclusive boundary, tags evidence with the sha256 value hash, and
      recomputes the fact at the observation time.
    * ``PostgresEvidenceStore.recompute`` uses only active evidence (the
      mutability window and the current value hash) and starts from the
      prior.
    * ``PostgresEvidenceStore.sweep`` targets non-static facts that have
      evidence; ``get_confidence`` raises ``KeyError`` for an unknown fact.
"""

from __future__ import annotations

import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from cortex.memory.evidence_store import (
    DEDUP_WINDOW,
    LR_BASE,
    PRIOR,
    WINDOW,
    Evidence,
    Observation,
    PostgresEvidenceStore,
    update_rule,
)
from cortex.memory.models import Fact, FactMutability

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = ROOT / "migrations" / "010_evidence.sql"

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class TestEvidenceMigration:
    """Migration 010 is the schema the production store depends on."""

    @pytest.mark.parametrize(
        "snippet",
        [
            "CREATE TABLE IF NOT EXISTS evidence",
            "fact_id UUID NOT NULL REFERENCES facts(id) ON DELETE CASCADE",
            "source_type VARCHAR(20) NOT NULL",
            "source_id TEXT NOT NULL",
            "value_hash TEXT NOT NULL",
            "strength FLOAT NOT NULL",
            "observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "CREATE INDEX IF NOT EXISTS idx_evidence_fact_time ON evidence(fact_id, observed_at)",
            "CREATE INDEX IF NOT EXISTS idx_evidence_time ON evidence(observed_at)",
        ],
    )
    def test_migration_contains_the_evidence_schema(self, snippet: str) -> None:
        assert snippet in MIGRATION_PATH.read_text()


class TestUpdateRule:
    """The pure update rule pinned by docs/specs/evidence-system.md."""

    def test_a_full_strength_observation_applies_the_full_lr(self) -> None:
        for source_type, lr in LR_BASE.items():
            shift = _logit(update_rule(PRIOR, 1.0, source_type)) - _logit(PRIOR)
            assert shift == pytest.approx(math.log(lr))

    def test_strength_half_is_neutral(self) -> None:
        for source_type in LR_BASE:
            assert update_rule(PRIOR, 0.5, source_type) == pytest.approx(PRIOR)

    def test_interpolation_is_linear_in_logit_space(self) -> None:
        for source_type, lr in LR_BASE.items():
            for strength, factor in ((0.75, 0.5), (0.25, -0.5)):
                shift = _logit(update_rule(PRIOR, strength, source_type)) - _logit(PRIOR)
                assert shift == pytest.approx(factor * math.log(lr))

    def test_boundaries_saturate(self) -> None:
        assert update_rule(1.0, 1.0, "user_confirm") == 1.0
        assert update_rule(0.0, 0.0, "sensor") == 0.0
        assert 0.0 <= update_rule(0.999999, 1.0, "user_confirm") <= 1.0
        assert 0.0 <= update_rule(1e-9, 0.0, "llm") <= 1.0

    def test_unknown_source_type_is_rejected(self) -> None:
        with pytest.raises(KeyError):
            update_rule(PRIOR, 1.0, "telepathy")


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def _value_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _evidence_row(
    *,
    fact_id: UUID,
    source_type: str = "sensor",
    source_id: str = "s1",
    value: str = "location.home",
    strength: float = 1.0,
    observed_at: datetime = T0,
) -> dict[str, Any]:
    return {
        "fact_id": fact_id,
        "source_type": source_type,
        "source_id": source_id,
        "value_hash": _value_hash(value),
        "strength": strength,
        "observed_at": observed_at,
    }


class TestPostgresEvidenceStore:
    """The async production store against a mocked ``DbSession``."""

    @pytest.fixture
    def db(self) -> MagicMock:
        mock = MagicMock()
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=None)
        mock.fetchrow = AsyncMock()
        mock.fetch = AsyncMock()
        mock.execute = AsyncMock()
        return mock

    @pytest.fixture
    def store(self, db: MagicMock) -> PostgresEvidenceStore:
        with patch("cortex.memory.evidence_store.DbSession") as db_session_class:
            db_session_class.return_value = db
            yield PostgresEvidenceStore()

    def test_default_session_factory_is_resolved_at_call_time(self) -> None:
        assert PostgresEvidenceStore()._session_factory is None

    @pytest.mark.asyncio
    async def test_injected_session_factory_is_used(self, db: MagicMock) -> None:
        seen: list[bool] = []

        @asynccontextmanager
        async def factory() -> AsyncIterator[Any]:
            seen.append(True)
            yield db

        store = PostgresEvidenceStore(session_factory=factory)
        db.fetchrow.return_value = {"confidence": 0.6}

        assert await store.get_confidence(uuid4()) == 0.6
        assert seen == [True]

    @pytest.mark.asyncio
    async def test_duplicate_within_the_inclusive_hour_is_discarded(
        self, store: PostgresEvidenceStore, db: MagicMock
    ) -> None:
        fact_id = uuid4()
        fact = Fact(id=fact_id, symbolic_repr="location.home", confidence=0.9)
        db.fetchrow.return_value = {"duplicate": 1}

        result = await store.record(
            fact, Observation("user_confirm", "s1", T0 + timedelta(minutes=30))
        )

        assert result is None
        db.fetchrow.assert_awaited_once()
        sql, *params = db.fetchrow.await_args.args
        assert "FROM evidence" in sql
        assert "observed_at >=" in sql  # inclusive at exactly one hour
        assert params == [
            fact.id,
            "user_confirm",
            "s1",
            _value_hash("location.home"),
            T0 + timedelta(minutes=30) - DEDUP_WINDOW,
        ]
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_record_inserts_evidence_and_recomputes_at_observation_time(
        self, store: PostgresEvidenceStore, db: MagicMock
    ) -> None:
        fact_id = uuid4()
        fact = Fact(
            id=fact_id,
            symbolic_repr="location.home",
            mutability=FactMutability.MUTABLE,
            confidence=0.9,
        )
        observation = Observation("sensor", "s1", T0)
        evidence_row = _evidence_row(fact_id=fact_id, strength=0.9)
        db.fetchrow.side_effect = [
            None,  # dedup miss
            evidence_row,  # INSERT ... RETURNING
            {"symbolic_repr": "location.home", "mutability": "mutable"},  # recompute fact
        ]
        db.fetch.return_value = [evidence_row]

        result = await store.record(fact, observation)

        assert result == Evidence(
            fact_id=fact_id,
            source_type="sensor",
            source_id="s1",
            value_hash=_value_hash("location.home"),
            strength=0.9,
            observed_at=T0,
        )
        insert_sql, *insert_params = db.fetchrow.call_args_list[1].args
        assert "INSERT INTO evidence" in insert_sql
        assert insert_params[3] == _value_hash("location.home")
        assert insert_params[4] == 0.9  # strength = the ingested fact confidence
        assert insert_params[5] == T0

        recompute_sql, *recompute_params = db.fetchrow.call_args_list[2].args
        assert "SELECT symbolic_repr, mutability FROM facts" in recompute_sql
        assert recompute_params == [fact_id]

        db.fetch.assert_awaited_once()
        fetch_sql, *fetch_params = db.fetch.await_args.args
        assert "FROM evidence" in fetch_sql
        assert "ORDER BY observed_at" in fetch_sql
        assert fetch_params == [fact_id]

        update_sql, *update_params = db.execute.await_args.args
        assert "UPDATE facts SET confidence = $2 WHERE id = $1" in update_sql
        assert update_params[0] == fact_id
        assert update_params[1] == pytest.approx(update_rule(PRIOR, 0.9, "sensor"))

    @pytest.mark.asyncio
    async def test_record_without_an_observation_is_a_no_op(
        self, store: PostgresEvidenceStore, db: MagicMock
    ) -> None:
        fact = Fact(symbolic_repr="location.home", confidence=0.9)

        assert await store.record(fact) is None
        db.fetchrow.assert_not_awaited()
        db.fetch.assert_not_awaited()
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_static_fact_is_pinned_without_an_evidence_row(
        self, store: PostgresEvidenceStore, db: MagicMock
    ) -> None:
        fact = Fact(
            id=uuid4(),
            symbolic_repr="user.birthdate",
            mutability=FactMutability.STATIC,
            confidence=0.3,
        )

        assert await store.record(fact, Observation("user_confirm", "s1", T0)) is None
        db.fetchrow.assert_not_awaited()
        db.fetch.assert_not_awaited()
        sql, *params = db.execute.await_args.args
        assert "UPDATE facts SET confidence = 1.0 WHERE id = $1" in sql
        assert params == [fact.id]

    @pytest.mark.asyncio
    async def test_get_confidence_returns_the_stored_value(
        self, store: PostgresEvidenceStore, db: MagicMock
    ) -> None:
        fact_id = uuid4()
        db.fetchrow.return_value = {"confidence": 0.75}

        assert await store.get_confidence(fact_id) == 0.75
        sql, *params = db.fetchrow.await_args.args
        assert "SELECT confidence FROM facts" in sql
        assert params == [fact_id]

    @pytest.mark.asyncio
    async def test_get_confidence_raises_key_error_for_an_unknown_fact(
        self, store: PostgresEvidenceStore, db: MagicMock
    ) -> None:
        db.fetchrow.return_value = None

        with pytest.raises(KeyError):
            await store.get_confidence(uuid4())

    @pytest.mark.asyncio
    async def test_recompute_of_an_unknown_fact_raises_key_error(
        self, store: PostgresEvidenceStore, db: MagicMock
    ) -> None:
        db.fetchrow.return_value = None

        with pytest.raises(KeyError):
            await store.recompute(uuid4(), now=T0)

    @pytest.mark.asyncio
    async def test_recompute_static_fact_pins_one_without_reading_evidence(
        self, store: PostgresEvidenceStore, db: MagicMock
    ) -> None:
        fact_id = uuid4()
        db.fetchrow.return_value = {
            "symbolic_repr": "user.birthdate",
            "mutability": "static",
        }

        await store.recompute(fact_id, now=T0)

        db.fetch.assert_not_awaited()
        sql, *params = db.execute.await_args.args
        assert "UPDATE facts SET confidence = 1.0 WHERE id = $1" in sql
        assert params == [fact_id]

    @pytest.mark.asyncio
    async def test_recompute_ignores_evidence_for_another_value(
        self, store: PostgresEvidenceStore, db: MagicMock
    ) -> None:
        fact_id = uuid4()
        db.fetchrow.return_value = {
            "symbolic_repr": "no.coffee",
            "mutability": "mutable",
        }
        db.fetch.return_value = [
            _evidence_row(fact_id=fact_id, value="likes.coffee", strength=1.0),
            _evidence_row(
                fact_id=fact_id,
                source_type="user_confirm",
                source_id="s2",
                value="no.coffee",
                strength=1.0,
            ),
        ]

        await store.recompute(fact_id, now=T0 + timedelta(hours=1))

        _, *params = db.execute.await_args.args
        expected = update_rule(PRIOR, 1.0, "user_confirm")
        assert params[1] == pytest.approx(expected)
        assert params[1] < update_rule(expected, 1.0, "user_confirm")

    @pytest.mark.asyncio
    async def test_recompute_drops_expired_ephemeral_evidence(
        self, store: PostgresEvidenceStore, db: MagicMock
    ) -> None:
        fact_id = uuid4()
        db.fetchrow.return_value = {
            "symbolic_repr": "active.app",
            "mutability": "ephemeral",
        }
        db.fetch.return_value = [_evidence_row(fact_id=fact_id, strength=1.0)]

        await store.recompute(fact_id, now=T0 + timedelta(days=1, hours=1))

        _, *params = db.execute.await_args.args
        assert params[1] == pytest.approx(PRIOR)

    @pytest.mark.asyncio
    async def test_recompute_counts_evidence_at_the_exact_window_boundary(
        self, store: PostgresEvidenceStore, db: MagicMock
    ) -> None:
        fact_id = uuid4()
        db.fetchrow.return_value = {
            "symbolic_repr": "boundary.app",
            "mutability": "ephemeral",
        }
        db.fetch.return_value = [
            _evidence_row(fact_id=fact_id, value="boundary.app", strength=1.0)
        ]

        await store.recompute(fact_id, now=T0 + timedelta(days=1))

        _, *params = db.execute.await_args.args
        assert params[1] == pytest.approx(update_rule(PRIOR, 1.0, "sensor"))
        assert params[1] > PRIOR

    @pytest.mark.asyncio
    async def test_recompute_starts_from_the_prior_over_all_active_evidence(
        self, store: PostgresEvidenceStore, db: MagicMock
    ) -> None:
        fact_id = uuid4()
        db.fetchrow.return_value = {
            "symbolic_repr": "location.home",
            "mutability": "mutable",
        }
        db.fetch.return_value = [
            _evidence_row(fact_id=fact_id, source_type="user_confirm", strength=1.0),
            _evidence_row(
                fact_id=fact_id,
                source_type="sensor",
                source_id="s2",
                strength=0.8,
                observed_at=T0 + timedelta(minutes=10),
            ),
        ]

        await store.recompute(fact_id, now=T0 + timedelta(hours=1))

        _, *params = db.execute.await_args.args
        expected = update_rule(
            update_rule(PRIOR, 1.0, "user_confirm"), 0.8, "sensor"
        )
        assert params[1] == pytest.approx(expected)

    @pytest.mark.asyncio
    async def test_sweep_recomputes_non_static_facts_that_have_evidence(
        self, store: PostgresEvidenceStore, db: MagicMock
    ) -> None:
        first, second = uuid4(), uuid4()
        db.fetch.return_value = [{"fact_id": first}, {"fact_id": second}]
        store.recompute = AsyncMock()

        await store.sweep(T0)

        sql = db.fetch.await_args.args[0]
        assert "SELECT DISTINCT" in sql
        assert "FROM evidence" in sql
        assert "JOIN facts" in sql
        assert "mutability <> 'static'" in sql
        assert store.recompute.await_count == 2
        store.recompute.assert_any_await(first, now=T0)
        store.recompute.assert_any_await(second, now=T0)

    @pytest.mark.asyncio
    async def test_recompute_without_a_fact_id_sweeps(
        self, store: PostgresEvidenceStore, db: MagicMock
    ) -> None:
        db.fetch.return_value = []
        store.sweep = AsyncMock()

        await store.recompute(None, now=T0)

        store.sweep.assert_awaited_once_with(T0)

    @pytest.mark.asyncio
    async def test_evidence_for_maps_rows_and_orders_by_time(
        self, store: PostgresEvidenceStore, db: MagicMock
    ) -> None:
        fact_id = uuid4()
        later = _evidence_row(
            fact_id=fact_id,
            source_id="s2",
            observed_at=T0 + timedelta(hours=2),
        )
        earlier = _evidence_row(fact_id=fact_id, observed_at=T0)
        db.fetch.return_value = [earlier, later]

        rows = await store.evidence_for(fact_id)

        assert rows == [
            Evidence(
                fact_id=fact_id,
                source_type="sensor",
                source_id="s1",
                value_hash=_value_hash("location.home"),
                strength=1.0,
                observed_at=T0,
            ),
            Evidence(
                fact_id=fact_id,
                source_type="sensor",
                source_id="s2",
                value_hash=_value_hash("location.home"),
                strength=1.0,
                observed_at=T0 + timedelta(hours=2),
            ),
        ]
        sql, *params = db.fetch.await_args.args
        assert "FROM evidence" in sql
        assert "ORDER BY observed_at" in sql
        assert params == [fact_id]


class TestCalibrationConstants:
    """The constants are exported and pinned for the tests that consume them."""

    def test_windows(self) -> None:
        assert WINDOW[FactMutability.EPHEMERAL] == timedelta(days=1)
        assert WINDOW[FactMutability.MUTABLE] == timedelta(days=182)
        assert WINDOW[FactMutability.SEMI_STATIC] == timedelta(days=1825)
        assert WINDOW[FactMutability.STATIC] is None

    def test_prior_and_dedup_window(self) -> None:
        assert PRIOR == 0.5
        assert DEDUP_WINDOW == timedelta(hours=1)
        assert LR_BASE == {"user_confirm": 20.0, "sensor": 10.0, "llm": 3.0}
