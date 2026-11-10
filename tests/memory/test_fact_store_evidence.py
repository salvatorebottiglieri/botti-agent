"""FactStore integration with the evidence engine (issue #83).

Invariants under test:
    * A new non-static fact is stored at the prior (0.5) and its first
      observation moves the confidence with the same rule as every later
      observation.
    * A duplicate observation within the one-hour inclusive window is
      discarded and the confidence moves exactly once.
    * Changing a fact's value stops old evidence contributing without
      deleting it; reverting restores it.
    * Static facts are pinned at 1.0, never record evidence, and cannot be
      retracted.
    * An incoming static observation cannot promote an existing non-static
      fact, and is recorded as evidence for the existing mutability.
    * Without an evidence store the legacy confidence write is preserved.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from cortex.memory.evidence_store import (
    PRIOR,
    Evidence,
    EvidenceStore,
    Observation,
    update_rule,
)
from cortex.memory.fact_store import FactStore
from cortex.memory.models import Fact, FactMutability

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class FakeFactRepository:
    """In-memory FactRepository stand-in that generates ids like Postgres."""

    def __init__(self) -> None:
        self.facts: dict[UUID, Fact] = {}
        self.stored_confidences: list[float] = []
        self.update_keys: list[set[str]] = []
        self.retracted: list[UUID] = []

    async def get_by_symbolic_repr(self, symbolic_repr: str) -> Fact | None:
        for fact in self.facts.values():
            if fact.symbolic_repr == symbolic_repr and fact.retracted_at is None:
                return fact
        return None

    async def store(self, fact: Fact) -> Fact:
        self.stored_confidences.append(fact.confidence)
        stored = replace(fact)
        stored.id = uuid4()  # the repository's INSERT does not send the model id
        self.facts[stored.id] = stored
        return stored

    async def update(self, fact_id: UUID, updates: dict[str, Any]) -> Fact | None:
        self.update_keys.append(set(updates))
        fact = self.facts.get(fact_id)
        if fact is None:
            return None
        for key, value in updates.items():
            setattr(fact, key, value)
        return fact

    async def get(self, fact_id: UUID) -> Fact | None:
        return self.facts.get(fact_id)

    async def retract(self, fact_id: UUID, reason: str | None = None) -> None:
        self.retracted.append(fact_id)
        fact = self.facts.get(fact_id)
        if fact is not None:
            fact.retracted_at = datetime.now(UTC)


class FakeAsyncEvidenceStore:
    """Async adapter over the in-memory EvidenceStore.

    The real Postgres store reads the fact's current value from ``facts`` on
    every recompute and writes the posterior back to ``facts.confidence``.
    This adapter mirrors both effects so FactStore integration is exercised
    against the contract implementation.
    """

    def __init__(self, repository: FakeFactRepository) -> None:
        self._repository = repository
        self._store = EvidenceStore()

    async def record(
        self, fact: Fact, observation: Observation | None = None
    ) -> Evidence | None:
        row = self._store.record(fact, observation)
        if row is not None or FactMutability(fact.mutability) == FactMutability.STATIC:
            await self._sync_confidence(fact.id)
        return row

    async def get_confidence(self, fact_id: UUID) -> float:
        return self._store.get_confidence(fact_id)

    async def recompute(self, fact_id: UUID | None = None, *, now: datetime) -> None:
        if fact_id is None:
            await self.sweep(now)
            return
        await self._sync_value(fact_id)
        self._store.recompute(fact_id, now=now)
        await self._sync_confidence(fact_id)

    async def sweep(self, now: datetime) -> None:
        for fact_id in list(self._repository.facts):
            await self._sync_value(fact_id)
        self._store.sweep(now)
        for fact_id in list(self._repository.facts):
            await self._sync_confidence(fact_id)

    async def evidence_for(self, fact_id: UUID) -> list[Evidence]:
        return self._store.evidence_for(fact_id)

    async def _sync_value(self, fact_id: UUID) -> None:
        fact = await self._repository.get(fact_id)
        if fact is not None and fact_id in self._store._facts:
            self._store.record(fact)  # value-change sync: no evidence, no recompute

    async def _sync_confidence(self, fact_id: UUID) -> None:
        fact = await self._repository.get(fact_id)
        if fact is not None:
            fact.confidence = self._store.get_confidence(fact_id)


def _fact(
    *,
    symbolic_repr: str,
    confidence: float = 1.0,
    mutability: FactMutability | str = FactMutability.MUTABLE,
) -> Fact:
    return Fact(
        symbolic_repr=symbolic_repr,
        natural_lang_repr=symbolic_repr.replace(".", " "),
        confidence=confidence,
        mutability=mutability,  # type: ignore[arg-type]
    )


class TestNewFact:
    """A new fact is stored at the prior and the observation applies the rule."""

    @pytest.mark.asyncio
    async def test_first_observation_moves_confidence_from_the_prior(self) -> None:
        repo = FakeFactRepository()
        evidence = FakeAsyncEvidenceStore(repo)
        store = FactStore(repo, evidence_store=evidence)

        with patch("cortex.memory.fact_store._utcnow", return_value=T0):
            stored = await store.add_fact(
                _fact(symbolic_repr="likes.coffee", confidence=1.0),
                source_type="user_confirm",
                source_id="s1",
            )

        assert repo.stored_confidences == [PRIOR]  # stored at 0.5, then recomputed
        assert stored.confidence == pytest.approx(
            update_rule(PRIOR, 1.0, "user_confirm")
        )
        assert stored.confidence > PRIOR
        rows = await evidence.evidence_for(stored.id)
        assert len(rows) == 1
        assert rows[0].source_type == "user_confirm"
        assert rows[0].source_id == "s1"
        assert rows[0].observed_at == T0
        assert rows[0].strength == 1.0

    @pytest.mark.asyncio
    async def test_observation_strength_is_the_incoming_fact_confidence(self) -> None:
        repo = FakeFactRepository()
        evidence = FakeAsyncEvidenceStore(repo)
        store = FactStore(repo, evidence_store=evidence)

        with patch("cortex.memory.fact_store._utcnow", return_value=T0):
            stored = await store.add_fact(
                _fact(symbolic_repr="location.home", confidence=0.7),
                source_type="sensor",
                source_id="gps-1",
            )

        (row,) = await evidence.evidence_for(stored.id)
        assert row.strength == 0.7
        assert stored.confidence == pytest.approx(update_rule(PRIOR, 0.7, "sensor"))


class TestDedup:
    """Repeated identical observations count once within the window."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("delta", "expected_rows"),
        [
            (timedelta(minutes=30), 1),
            (timedelta(hours=1), 1),  # inclusive boundary
            (timedelta(hours=2), 2),
        ],
    )
    async def test_duplicate_window_is_inclusive(
        self, delta: timedelta, expected_rows: int
    ) -> None:
        repo = FakeFactRepository()
        evidence = FakeAsyncEvidenceStore(repo)
        store = FactStore(repo, evidence_store=evidence)

        with patch("cortex.memory.fact_store._utcnow", return_value=T0):
            first = await store.add_fact(
                _fact(symbolic_repr="likes.coffee"), source_type="llm", source_id="extractor-1"
            )
        first_confidence = first.confidence

        with patch("cortex.memory.fact_store._utcnow", return_value=T0 + delta):
            second = await store.add_fact(
                _fact(symbolic_repr="likes.coffee"), source_type="llm", source_id="extractor-1"
            )

        assert len(await evidence.evidence_for(first.id)) == expected_rows
        if expected_rows == 1:
            assert second.confidence == pytest.approx(first_confidence)
        else:
            assert second.confidence > first_confidence

    @pytest.mark.asyncio
    async def test_a_different_source_counts_as_new_evidence(self) -> None:
        repo = FakeFactRepository()
        evidence = FakeAsyncEvidenceStore(repo)
        store = FactStore(repo, evidence_store=evidence)

        with patch("cortex.memory.fact_store._utcnow", return_value=T0):
            first = await store.add_fact(
                _fact(symbolic_repr="likes.coffee"), source_type="llm", source_id="extractor-1"
            )
            first_confidence = first.confidence
            second = await store.add_fact(
                _fact(symbolic_repr="likes.coffee"), source_type="sensor", source_id="extractor-1"
            )

        assert len(await evidence.evidence_for(first.id)) == 2
        assert second.confidence > first_confidence


class TestValueChange:
    """Value-tagged evidence stops and resumes contributing."""

    @pytest.mark.asyncio
    async def test_change_stops_old_evidence_and_revert_restores_it(self) -> None:
        repo = FakeFactRepository()
        evidence = FakeAsyncEvidenceStore(repo)
        store = FactStore(repo, evidence_store=evidence)

        with patch("cortex.memory.fact_store._utcnow", return_value=T0):
            stored = await store.add_fact(
                _fact(symbolic_repr="likes.coffee"),
                source_type="user_confirm",
                source_id="s1",
            )
        assert stored.confidence > PRIOR

        # A value change keeps the same fact row (same id) and its history.
        repo.facts[stored.id].symbolic_repr = "no.coffee"
        await evidence.recompute(stored.id, now=T0 + timedelta(hours=1))

        assert await evidence.get_confidence(stored.id) == pytest.approx(PRIOR)
        assert len(await evidence.evidence_for(stored.id)) == 1  # retained, inactive

        repo.facts[stored.id].symbolic_repr = "likes.coffee"
        await evidence.sweep(T0 + timedelta(hours=2))

        assert await evidence.get_confidence(stored.id) > PRIOR  # old evidence active


class TestStaticFacts:
    """Static facts are pinned and outside the evidence machinery."""

    @pytest.mark.asyncio
    async def test_static_fact_is_pinned_has_no_evidence_and_is_not_retracted(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        repo = FakeFactRepository()
        evidence = FakeAsyncEvidenceStore(repo)
        store = FactStore(repo, evidence_store=evidence)

        with patch("cortex.memory.fact_store._utcnow", return_value=T0):
            stored = await store.add_fact(
                _fact(
                    symbolic_repr="user.birthdate",
                    confidence=0.3,
                    mutability=FactMutability.STATIC,
                ),
                source_type="user_confirm",
                source_id="s1",
            )

        assert stored.confidence == 1.0
        assert await evidence.evidence_for(stored.id) == []

        with caplog.at_level("WARNING", logger="cortex.memory.fact_store"):
            assert await store.retract_fact(stored.id) is False
        assert repo.retracted == []
        assert any("static" in record.message.lower() for record in caplog.records)

    @pytest.mark.asyncio
    async def test_retraction_checks_a_raw_string_mutability(self) -> None:
        """DB rows may carry the raw string, so identity is normalized."""
        repo = FakeFactRepository()
        fact = _fact(symbolic_repr="user.birthdate", mutability=FactMutability.STATIC)
        stored = await repo.store(fact)
        repo.facts[stored.id].mutability = "static"  # type: ignore[assignment]

        assert await FactStore(repo).retract_fact(stored.id) is False
        assert repo.retracted == []


class TestIncomingStatic:
    """An incoming static cannot promote an existing non-static fact."""

    @pytest.mark.asyncio
    async def test_incoming_static_is_recorded_as_evidence_for_the_existing_mutability(
        self,
    ) -> None:
        repo = FakeFactRepository()
        evidence = FakeAsyncEvidenceStore(repo)
        store = FactStore(repo, evidence_store=evidence)

        with patch("cortex.memory.fact_store._utcnow", return_value=T0):
            first = await store.add_fact(
                _fact(symbolic_repr="user.birthplace", confidence=0.8),
                source_type="llm",
                source_id="extractor-1",
            )
            first_confidence = first.confidence
            second = await store.add_fact(
                _fact(
                    symbolic_repr="user.birthplace",
                    confidence=1.0,
                    mutability=FactMutability.STATIC,
                ),
                source_type="llm",
                source_id="extractor-2",
            )

        assert second.mutability == FactMutability.MUTABLE
        assert second.confidence < 1.0
        assert second.confidence > first_confidence
        assert "confidence" not in repo.update_keys[-1]
        rows = await evidence.evidence_for(first.id)
        assert len(rows) == 2  # the static observation became evidence


class TestNoEvidenceStore:
    """Without an evidence store FactStore keeps the legacy write path."""

    @pytest.mark.asyncio
    async def test_new_fact_keeps_its_confidence_and_records_no_evidence(self) -> None:
        repo = FakeFactRepository()
        store = FactStore(repo)

        with patch("cortex.memory.fact_store._utcnow", return_value=T0):
            stored = await store.add_fact(
                _fact(symbolic_repr="fact.plain", confidence=0.9),
                source_type="sensor",
                source_id="s1",
            )

        assert repo.stored_confidences == [0.9]
        assert repo.update_keys == []
        assert stored.confidence == 0.9

    @pytest.mark.asyncio
    async def test_existing_fact_update_still_carries_confidence(self) -> None:
        repo = FakeFactRepository()
        store = FactStore(repo)
        with patch("cortex.memory.fact_store._utcnow", return_value=T0):
            await store.add_fact(_fact(symbolic_repr="fact.plain", confidence=0.9))
            updated = await store.add_fact(
                _fact(symbolic_repr="fact.plain", confidence=0.2)
            )

        assert repo.update_keys[-1] == {"natural_lang_repr", "payload", "confidence"}
        assert updated.confidence == 0.2
