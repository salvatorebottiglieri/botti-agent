"""Evidence engine: fact confidence as the posterior over active evidence.

Two implementations share the calibration constants and the update rule:

* :class:`EvidenceStore` — the synchronous, no-argument in-memory store. It is
  the executable E1 contract used by ``tests/eval/test_evidence_invariants.py``
  and is a legitimate in-memory implementation, not dead code.
* :class:`PostgresEvidenceStore` — the asynchronous production store used by
  ``FactStore`` and the hourly sweep. It owns the ``evidence`` table (migration
  ``010_evidence.sql``) and writes the recomputed posterior back to
  ``facts.confidence``.

The data model, ingestion flow, calibration constants and invariants are
specified in ``docs/specs/evidence-system.md``; the rationale is ADR-0013.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID

from cortex.db.session import DbSession
from cortex.memory.models import Fact, FactMutability

# --- Calibration constants (docs/specs/evidence-system.md — the only magic numbers) ---

LR_BASE: dict[str, float] = {"user_confirm": 20.0, "sensor": 10.0, "llm": 3.0}
PRIOR = 0.5
DEDUP_WINDOW = timedelta(hours=1)
WINDOW: dict[FactMutability, timedelta | None] = {
    FactMutability.EPHEMERAL: timedelta(days=1),
    FactMutability.MUTABLE: timedelta(days=182),  # 6 months
    FactMutability.SEMI_STATIC: timedelta(days=1825),  # 5 years
    FactMutability.STATIC: None,
}

_FACT_COLUMNS = "symbolic_repr, mutability"
_EVIDENCE_COLUMNS = (
    "fact_id, source_type, source_id, value_hash, strength, observed_at"
)


@dataclass(frozen=True)
class Observation:
    """Metadata of the observation that triggered a fact write (``record``)."""

    source_type: str
    source_id: str
    observed_at: datetime


@dataclass(frozen=True)
class Evidence:
    """One accepted observation row in the evidence table."""

    fact_id: UUID
    source_type: str
    source_id: str
    value_hash: str
    strength: float
    observed_at: datetime


def _value_hash(value: str) -> str:
    """The contract's value_hash: sha256 hex of the UTF-8 value."""
    return sha256(value.encode("utf-8")).hexdigest()


def update_rule(confidence: float, strength: float, source_type: str) -> float:
    """Logit-space Bayesian update.

    ``logit(p') = logit(p) + (2·strength − 1) · ln(LR(source_type))``.
    Saturated at the boundaries: ``p <= 0 → 0`` and ``p >= 1 → 1``.
    """
    if confidence <= 0.0:
        return 0.0
    if confidence >= 1.0:
        return 1.0
    lr = LR_BASE[source_type]
    logit = math.log(confidence / (1.0 - confidence))
    logit_next = logit + (2.0 * strength - 1.0) * math.log(lr)
    return 1.0 / (1.0 + math.exp(-logit_next))


@dataclass
class _StoredFact:
    value: str
    mutability: FactMutability
    confidence: float


class EvidenceStore:
    """Synchronous in-memory evidence store (the E1 contract implementation).

    Mirrors the reference behaviour in ``tests/eval/test_evidence_invariants.py``:
    dedup, value tagging, mutability windows, static pinning, and the logit
    update rule. The production store is :class:`PostgresEvidenceStore`.
    """

    def __init__(self) -> None:
        self._facts: dict[UUID, _StoredFact] = {}
        self._evidence: list[Evidence] = []

    def record(self, fact: Fact, observation: Observation | None = None) -> Evidence | None:
        """Ingest one fact write, returning the evidence row if accepted."""
        stored = self._facts.get(fact.id)
        if stored is None:
            stored = _StoredFact(
                value=fact.symbolic_repr,
                mutability=FactMutability(fact.mutability),
                confidence=PRIOR,
            )
            self._facts[fact.id] = stored
        else:
            stored.value = fact.symbolic_repr
            stored.mutability = FactMutability(fact.mutability)

        if stored.mutability == FactMutability.STATIC:
            stored.confidence = 1.0  # pinned, never moved, never retracted
            return None

        if observation is None:
            return None  # value-change sync: no evidence, no recompute here

        value_hash = _value_hash(fact.symbolic_repr)
        cutoff = observation.observed_at - DEDUP_WINDOW
        for row in self.evidence_for(fact.id):
            if (
                row.source_type == observation.source_type
                and row.source_id == observation.source_id
                and row.value_hash == value_hash
                and row.observed_at >= cutoff
            ):
                return None  # duplicate within the dedup window — discarded

        row = Evidence(
            fact_id=fact.id,
            source_type=observation.source_type,
            source_id=observation.source_id,
            value_hash=value_hash,
            strength=fact.confidence,
            observed_at=observation.observed_at,
        )
        self._evidence.append(row)
        self._recompute(fact.id, observation.observed_at)
        return row

    def get_confidence(self, fact_id: UUID) -> float:
        """The stored confidence; ``KeyError`` for an unseen fact."""
        return self._facts[fact_id].confidence

    def recompute(self, fact_id: UUID | None = None, *, now: datetime) -> None:
        """Replace the stored confidence with the posterior at ``now``."""
        if fact_id is None:
            self.sweep(now)
        else:
            self._recompute(fact_id, now)

    def sweep(self, now: datetime) -> None:
        """Recompute every known fact at ``now``."""
        for fact_id in list(self._facts):
            self._recompute(fact_id, now)

    def evidence_for(self, fact_id: UUID) -> list[Evidence]:
        """All evidence rows for the fact, ordered by ``observed_at``."""
        return sorted(
            (row for row in self._evidence if row.fact_id == fact_id),
            key=lambda row: row.observed_at,
        )

    def _recompute(self, fact_id: UUID, now: datetime) -> None:
        stored = self._facts[fact_id]
        if stored.mutability == FactMutability.STATIC:
            stored.confidence = 1.0
            return
        window = WINDOW[stored.mutability]
        assert window is not None  # non-static facts always have a window
        current_hash = _value_hash(stored.value)
        posterior = PRIOR
        for row in self.evidence_for(fact_id):
            if row.observed_at < now - window:
                continue  # window expired — no longer contributes
            if row.value_hash != current_hash:
                continue  # value no longer matches — no longer contributes
            posterior = update_rule(posterior, row.strength, row.source_type)
        stored.confidence = posterior


class PostgresEvidenceStore:
    """Asynchronous evidence store over the ``evidence`` table.

    The session factory defaults to the shared pooled ``DbSession`` and is
    resolved at call time, so the CLI can inject an adapter over its own
    connection and tests can inject a mock.
    """

    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
    ) -> None:
        self._session_factory = session_factory

    def _session(self) -> AbstractAsyncContextManager[Any]:
        """The injected session or the shared pool."""
        return self._session_factory() if self._session_factory is not None else DbSession()

    async def record(self, fact: Fact, observation: Observation | None = None) -> Evidence | None:
        """Ingest one fact write and recompute the fact's confidence.

        Static facts are pinned at 1.0 and never create evidence. An
        observation duplicate within the inclusive one-hour window is
        discarded. A value-change sync (``observation is None``) records no
        evidence and does not recompute.
        """
        if FactMutability(fact.mutability) == FactMutability.STATIC:
            async with self._session() as db:
                await db.execute(
                    "UPDATE facts SET confidence = 1.0 WHERE id = $1", fact.id
                )
            return None

        if observation is None:
            return None

        value_hash = _value_hash(fact.symbolic_repr)
        cutoff = observation.observed_at - DEDUP_WINDOW
        async with self._session() as db:
            duplicate = await db.fetchrow(
                """
                SELECT 1 FROM evidence
                WHERE fact_id = $1 AND source_type = $2 AND source_id = $3
                  AND value_hash = $4 AND observed_at >= $5
                LIMIT 1
                """,
                fact.id,
                observation.source_type,
                observation.source_id,
                value_hash,
                cutoff,
            )
            if duplicate is not None:
                return None

            row = await db.fetchrow(
                f"""
                INSERT INTO evidence (fact_id, source_type, source_id, value_hash,
                                      strength, observed_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING {_EVIDENCE_COLUMNS}
                """,
                fact.id,
                observation.source_type,
                observation.source_id,
                value_hash,
                fact.confidence,
                observation.observed_at,
            )

        await self.recompute(fact.id, now=observation.observed_at)
        return self._row_to_evidence(row)

    async def get_confidence(self, fact_id: UUID) -> float:
        """The stored ``facts.confidence``; ``KeyError`` for an unknown fact."""
        async with self._session() as db:
            row = await db.fetchrow(
                "SELECT confidence FROM facts WHERE id = $1", fact_id
            )
        if row is None:
            raise KeyError(fact_id)
        return float(row["confidence"])

    async def recompute(self, fact_id: UUID | None = None, *, now: datetime) -> None:
        """Replace the stored confidence with the posterior over active evidence.

        Active evidence is within the fact's mutability window and matches the
        fact's current ``symbolic_repr``. The posterior is recomputed from the
        prior, never accumulated from the previous stored value.
        """
        if fact_id is None:
            await self.sweep(now)
            return

        async with self._session() as db:
            fact_row = await db.fetchrow(
                f"SELECT {_FACT_COLUMNS} FROM facts WHERE id = $1", fact_id
            )
            if fact_row is None:
                raise KeyError(fact_id)

            mutability = FactMutability(fact_row["mutability"])
            if mutability == FactMutability.STATIC:
                await db.execute(
                    "UPDATE facts SET confidence = 1.0 WHERE id = $1", fact_id
                )
                return

            rows = await db.fetch(
                f"""
                SELECT {_EVIDENCE_COLUMNS}
                FROM evidence
                WHERE fact_id = $1
                ORDER BY observed_at
                """,
                fact_id,
            )
            window = WINDOW[mutability]
            assert window is not None  # non-static facts always have a window
            current_hash = _value_hash(fact_row["symbolic_repr"])
            posterior = PRIOR
            for row in rows:
                if row["observed_at"] < now - window:
                    continue  # window expired — retained but inactive
                if row["value_hash"] != current_hash:
                    continue  # value no longer matches — retained but inactive
                posterior = update_rule(posterior, row["strength"], row["source_type"])

            await db.execute(
                "UPDATE facts SET confidence = $2 WHERE id = $1", fact_id, posterior
            )

    async def sweep(self, now: datetime) -> None:
        """Recompute every non-static fact that has evidence at ``now``."""
        async with self._session() as db:
            rows = await db.fetch(
                """
                SELECT DISTINCT e.fact_id
                FROM evidence e
                JOIN facts f ON f.id = e.fact_id
                WHERE f.mutability <> 'static'
                """
            )
        for row in rows:
            await self.recompute(row["fact_id"], now=now)

    async def evidence_for(self, fact_id: UUID) -> list[Evidence]:
        """All evidence rows for the fact, ordered by ``observed_at``."""
        async with self._session() as db:
            rows = await db.fetch(
                f"""
                SELECT {_EVIDENCE_COLUMNS}
                FROM evidence
                WHERE fact_id = $1
                ORDER BY observed_at
                """,
                fact_id,
            )
        return [self._row_to_evidence(row) for row in rows]

    def _row_to_evidence(self, row: Any) -> Evidence:
        """Convert a database row to an :class:`Evidence` value."""
        return Evidence(
            fact_id=row["fact_id"],
            source_type=row["source_type"],
            source_id=row["source_id"],
            value_hash=row["value_hash"],
            strength=row["strength"],
            observed_at=row["observed_at"],
        )
