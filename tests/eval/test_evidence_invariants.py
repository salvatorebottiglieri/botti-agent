"""E1 — evidence-invariants suite.

Formalizes the invariants in ``docs/evidence-system.md`` as deterministic eval
tests, written against the evidence engine's *contract* so the regression
oracle exists **before** the engine (issue #83). Suite id: E1.

Source of truth
---------------
``docs/evidence-system.md`` (data model, ingestion flow, update rule,
calibration constants, Invariants table) and ``docs/adr/0013`` (rationale).
When #83 lands, this module must keep passing with **zero code changes**: the
``engine-#83`` fixture parameter below is gated by ``pytest.importorskip`` and
goes live the moment ``cortex.memory.evidence_store`` exists, becoming the E1
PR gate. Until then every engine-bound test skips with reason
``"evidence engine #83 not landed"`` — never a hard failure.

The #83 seam contract (this docstring is part of the implementation contract)
-----------------------------------------------------------------------------
``cortex.memory.evidence_store`` MUST export:

* ``EvidenceStore`` — class, no-argument constructor. Methods:
  - ``record(fact, observation=None) -> Evidence | None`` — ingest one fact
    write (``FactStore.add_fact`` → ``record``). ``observation`` carries the
    source metadata; ``None`` means a pure value-change sync (no evidence).
    Returns the created evidence row, or ``None`` when the observation is
    discarded (dedup), the fact is static, or no observation was supplied.
  - ``get_confidence(fact_id) -> float`` — the stored ``facts.confidence``.
    Raises ``KeyError`` for facts the store has never seen.
  - ``recompute(fact_id=None, *, now) -> None`` — replace the stored
    confidence with the posterior over active evidence at ``now``.
  - ``sweep(now) -> None`` — recompute every known fact at ``now`` (the
    periodic sweep; ``recompute(None, now)`` is equivalent).
  - ``evidence_for(fact_id) -> list[Evidence]`` — all evidence rows for the
    fact, ordered by ``observed_at`` (the audit/SELECT seam).
* ``update_rule(confidence, strength, source_type) -> float`` — the pure
  update rule, ``logit(p') = logit(p) + (2·strength − 1)·ln(LR(source_type))``.
  Saturates at the boundaries: ``p <= 0 → 0``, ``p >= 1 → 1``.
* Calibration constants, exported so tests can pin them:
  ``LR_BASE: dict[str, float]``, ``PRIOR: float``, ``DEDUP_WINDOW: timedelta``,
  ``WINDOW: dict[FactMutability, timedelta | None]``.
* ``Observation`` and ``Evidence`` value objects with the fields used below.

Decisions where the spec left signatures open (these bind #83)
--------------------------------------------------------------
* The "value" an evidence supports is the fact's ``symbolic_repr``; the
  observation's strength is the ingested ``fact.confidence``.
* ``value_hash`` = ``sha256(value.encode("utf-8")).hexdigest()``.
* Windows are deterministic calendar lengths: EPHEMERAL = 1 day,
  MUTABLE = 182 days (6 months), SEMI_STATIC = 1825 days (5 years),
  STATIC = none (no accumulation).
* Dedup window is inclusive: a row with the same
  ``(source_type, source_id, value_hash)`` and ``observed_at >= now − 1 h``
  discards the new observation.
* Active evidence iff ``observed_at >= now − window(mutability)`` AND
  ``value_hash`` matches the fact's current value. Expired rows are retained.
* ``record`` with an observation recomputes that fact's confidence at
  ``observed_at`` (ingestion step 4); value-change syncs do not recompute —
  tests drive recomputation through ``sweep``/``recompute`` with an explicit
  ``now`` so the suite never reads the wall clock.
* Static facts: ``record`` pins confidence at 1.0, never creates evidence
  rows, and returns ``None`` — the evidence-side enforcement of "never
  retracted".

Structure
---------
Every invariant in the ``docs/evidence-system.md`` Invariants table has a test
class here; each docstring states the law and its negation→test pair. Tests
run against the in-file reference implementation (``_ReferenceEvidenceStore``,
``_update_rule`` — the contract's executable spec, so the suite is provably
correct today) AND against the real engine once #83 lands (``engine-#83``
parameter, importorskip-gated). Deterministic: stdlib + ``math`` only, plus
the existing stdlib-only ``cortex.memory.models`` dataclasses for vocabulary
grounding. Runs under the eval pytest scope (T2 harness invocation).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Protocol
from uuid import UUID, uuid4

import pytest

from cortex.memory.models import Fact, FactMutability  # type: ignore[import-untyped]

ENGINE_MODULE = "cortex.memory.evidence_store"
ENGINE_SKIP_REASON = "evidence engine #83 not landed"

# --- Calibration constants (docs/evidence-system.md — the only magic numbers) ---

LR_BASE: dict[str, float] = {"user_confirm": 20.0, "sensor": 10.0, "llm": 3.0}
PRIOR = 0.5
DEDUP_WINDOW = timedelta(hours=1)
WINDOW: dict[FactMutability, timedelta | None] = {
    FactMutability.EPHEMERAL: timedelta(days=1),
    FactMutability.MUTABLE: timedelta(days=182),  # 6 months
    FactMutability.SEMI_STATIC: timedelta(days=1825),  # 5 years
    FactMutability.STATIC: None,
}

# --- The #83 seam: value objects and interface ---


@dataclass(frozen=True)
class Observation:
    """Metadata of the observation that triggered a fact write (record())."""

    source_type: str  # "sensor" | "user_confirm" | "llm"
    source_id: str  # stable id of the observing source
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


class EvidenceStore(Protocol):
    """The evidence engine seam that #83 must implement (see module docstring)."""

    def record(self, fact: Fact, observation: Observation | None = None) -> Evidence | None: ...

    def get_confidence(self, fact_id: UUID) -> float: ...

    def recompute(self, fact_id: UUID | None = None, *, now: datetime) -> None: ...

    def sweep(self, now: datetime) -> None: ...

    def evidence_for(self, fact_id: UUID) -> list[Evidence]: ...


@dataclass(frozen=True)
class EvidenceEngine:
    """The resolved seam surface the tests drive: reference spec or real #83 engine."""

    update_rule: Callable[[float, float, str], float]
    EvidenceStore: type[EvidenceStore]
    LR_BASE: dict[str, float]
    PRIOR: float
    DEDUP_WINDOW: timedelta
    WINDOW: dict[FactMutability, timedelta | None]


# --- Reference implementation: the contract's executable spec ---


def _value_hash(value: str) -> str:
    """The contract's value_hash: sha256 hex of the UTF-8 value."""
    return sha256(value.encode("utf-8")).hexdigest()


def _update_rule(confidence: float, strength: float, source_type: str) -> float:
    """Reference update rule: logit-space Bayesian update (docs/evidence-system.md)."""
    if confidence <= 0.0:
        return 0.0
    if confidence >= 1.0:
        return 1.0
    lr = LR_BASE[source_type]
    logit = math.log(confidence / (1.0 - confidence))
    logit_next = logit + (2.0 * strength - 1.0) * math.log(lr)
    return 1.0 / (1.0 + math.exp(-logit_next))


def _logit(p: float) -> float:
    """Logit transform: ln(p / (1 − p))."""
    return math.log(p / (1.0 - p))


@dataclass
class _StoredFact:
    value: str
    mutability: FactMutability
    confidence: float


class _ReferenceEvidenceStore:
    """In-memory executable spec of the evidence contract.

    Implements docs/evidence-system.md exactly: dedup, value-tagging, windows,
    static facts, and the logit update rule. This is the contract's reference
    oracle — the #83 engine must reproduce its observable behavior.
    """

    def __init__(self) -> None:
        self._facts: dict[UUID, _StoredFact] = {}
        self._evidence: list[Evidence] = []

    def record(self, fact: Fact, observation: Observation | None = None) -> Evidence | None:
        stored = self._facts.get(fact.id)
        if stored is None:
            stored = _StoredFact(
                value=fact.symbolic_repr,
                mutability=fact.mutability,
                confidence=PRIOR,
            )
            self._facts[fact.id] = stored
        else:
            stored.value = fact.symbolic_repr
            stored.mutability = fact.mutability

        if fact.mutability is FactMutability.STATIC:
            stored.confidence = 1.0  # pinned, never moved, never retracted
            return None

        if observation is None:
            return None  # value-change sync: no evidence, no recompute here

        value_hash = _value_hash(fact.symbolic_repr)
        cutoff = observation.observed_at - DEDUP_WINDOW
        for row in self._evidence_for(fact.id):
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
        return self._facts[fact_id].confidence

    def recompute(self, fact_id: UUID | None = None, *, now: datetime) -> None:
        if fact_id is None:
            self.sweep(now)
        else:
            self._recompute(fact_id, now)

    def sweep(self, now: datetime) -> None:
        for fact_id in list(self._facts):
            self._recompute(fact_id, now)

    def evidence_for(self, fact_id: UUID) -> list[Evidence]:
        return self._evidence_for(fact_id)

    def _evidence_for(self, fact_id: UUID) -> list[Evidence]:
        return sorted(
            (row for row in self._evidence if row.fact_id == fact_id),
            key=lambda row: row.observed_at,
        )

    def _recompute(self, fact_id: UUID, now: datetime) -> None:
        stored = self._facts[fact_id]
        if stored.mutability is FactMutability.STATIC:
            stored.confidence = 1.0
            return
        window = WINDOW[stored.mutability]
        assert window is not None  # non-static facts always have a window
        posterior = PRIOR
        for row in self._evidence_for(fact_id):
            if row.observed_at < now - window:
                continue  # window expired — no longer contributes
            if row.value_hash != _value_hash(stored.value):
                continue  # value no longer matches — no longer contributes
            posterior = _update_rule(posterior, row.strength, row.source_type)
        stored.confidence = posterior


@pytest.fixture(params=("reference", "engine"), ids=("reference-spec", "engine-#83"))
def engine(request: pytest.FixtureRequest) -> EvidenceEngine:
    """The seam under test: the in-file reference spec today, the engine after #83."""
    if request.param == "reference":
        return EvidenceEngine(
            update_rule=_update_rule,
            EvidenceStore=_ReferenceEvidenceStore,
            LR_BASE=LR_BASE,
            PRIOR=PRIOR,
            DEDUP_WINDOW=DEDUP_WINDOW,
            WINDOW=WINDOW,
        )
    module = pytest.importorskip(
        ENGINE_MODULE, reason=ENGINE_SKIP_REASON, exc_type=ModuleNotFoundError
    )
    return EvidenceEngine(
        update_rule=module.update_rule,
        EvidenceStore=module.EvidenceStore,
        LR_BASE=module.LR_BASE,
        PRIOR=module.PRIOR,
        DEDUP_WINDOW=module.DEDUP_WINDOW,
        WINDOW=module.WINDOW,
    )


# --- Deterministic test helpers (no wall clock, no LLM, no DB) ---

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _fact(
    *,
    value: str,
    fact_id: UUID | None = None,
    confidence: float = 1.0,
    mutability: FactMutability = FactMutability.MUTABLE,
) -> Fact:
    return Fact(id=fact_id or uuid4(), symbolic_repr=value, confidence=confidence, mutability=mutability)


def _obs(source_type: str, source_id: str, at: datetime) -> Observation:
    return Observation(source_type=source_type, source_id=source_id, observed_at=at)


def _expected_posterior(
    engine: EvidenceEngine,
    store: EvidenceStore,
    fact_id: UUID,
    now: datetime,
    *,
    value: str,
    mutability: FactMutability,
) -> float:
    """Independent posterior over *active* evidence, per the documented contract.

    Active = within the mutability window AND value_hash matches the current
    value. This mirrors the contract, not the store under test — a store that
    filters wrongly (or not at all) fails the comparison.
    """
    window = engine.WINDOW[mutability]
    posterior = engine.PRIOR
    for row in store.evidence_for(fact_id):
        if window is not None and row.observed_at < now - window:
            continue  # window expired
        if row.value_hash != _value_hash(value):
            continue  # value no longer matches
        posterior = engine.update_rule(posterior, row.strength, row.source_type)
    return posterior


class TestStoredConfidenceEqualsPosterior:
    """Law: stored confidence equals the posterior over active evidence.

    Negation → this test fails when a store returns a confidence that differs
    from the posterior recomputed from its own active evidence rows (docs:
    'recompute from evidence and compare, after sweep').
    """

    def test_stored_equals_posterior_after_sweep(self, engine: EvidenceEngine) -> None:
        store = engine.EvidenceStore()
        fact = _fact(value="location.home", confidence=1.0)
        store.record(fact, _obs("user_confirm", "s1", T0))
        store.record(fact, _obs("sensor", "s2", T0 + timedelta(minutes=10)))
        now = T0 + timedelta(hours=1)
        store.sweep(now)

        expected = _expected_posterior(
            engine,
            store,
            fact.id,
            now,
            value="location.home",
            mutability=FactMutability.MUTABLE,
        )
        assert store.get_confidence(fact.id) == pytest.approx(expected)
        assert expected > engine.PRIOR  # the evidence actually moved it

    def test_after_expiry_sweep_still_matches_posterior(self, engine: EvidenceEngine) -> None:
        store = engine.EvidenceStore()
        fact = _fact(value="active.app", mutability=FactMutability.EPHEMERAL, confidence=1.0)
        store.record(fact, _obs("sensor", "s1", T0))
        now = T0 + timedelta(days=2)
        store.sweep(now)

        expected = _expected_posterior(
            engine,
            store,
            fact.id,
            now,
            value="active.app",
            mutability=FactMutability.EPHEMERAL,
        )
        assert store.get_confidence(fact.id) == pytest.approx(expected)
        assert expected == pytest.approx(engine.PRIOR)  # the only row has expired


class TestStaticFacts:
    """Law: static facts are pinned at 1.0 with zero evidence rows, never retracted.

    Negation → this test fails when a static fact accrues evidence rows, moves
    off 1.0, or accepts a retraction (docs: 'SELECT over static facts').
    """

    def test_static_fact_pinned_at_one_with_no_evidence(self, engine: EvidenceEngine) -> None:
        store = engine.EvidenceStore()
        fact = _fact(value="user.birthdate", mutability=FactMutability.STATIC, confidence=1.0)
        assert store.record(fact, _obs("user_confirm", "s1", T0)) is None
        assert store.get_confidence(fact.id) == 1.0
        assert store.evidence_for(fact.id) == []

    def test_static_fact_never_retracted_by_later_writes(self, engine: EvidenceEngine) -> None:
        store = engine.EvidenceStore()
        fact = _fact(value="user.birthdate", mutability=FactMutability.STATIC, confidence=1.0)
        store.record(fact, _obs("user_confirm", "s1", T0))
        # A later write — different value, contradiction strength — must not move it.
        store.record(
            _fact(fact_id=fact.id, value="user.birthdate2", mutability=FactMutability.STATIC, confidence=0.0),
            _obs("llm", "extractor-1", T0 + timedelta(hours=2)),
        )
        store.sweep(T0 + timedelta(days=1))
        assert store.get_confidence(fact.id) == 1.0
        assert store.evidence_for(fact.id) == []


class TestEvidenceWindowExpiry:
    """Law: no evidence contributes after its window expires.

    Negation → this test fails when expired evidence still moves the posterior
    (docs: 'posterior over active evidence ignores it').
    """

    def test_expired_ephemeral_evidence_stops_contributing(self, engine: EvidenceEngine) -> None:
        store = engine.EvidenceStore()
        fact = _fact(value="active.app", mutability=FactMutability.EPHEMERAL, confidence=1.0)
        store.record(fact, _obs("sensor", "s1", T0))
        assert store.get_confidence(fact.id) > engine.PRIOR

        store.sweep(T0 + timedelta(days=1) + timedelta(hours=1))
        assert store.get_confidence(fact.id) == pytest.approx(engine.PRIOR)

    def test_expired_evidence_row_is_retained_but_inactive(self, engine: EvidenceEngine) -> None:
        store = engine.EvidenceStore()
        fact = _fact(value="active.app", mutability=FactMutability.EPHEMERAL, confidence=1.0)
        store.record(fact, _obs("sensor", "s1", T0))
        store.sweep(T0 + timedelta(days=1) + timedelta(hours=1))
        rows = store.evidence_for(fact.id)
        assert len(rows) == 1  # old evidence is not deleted...
        assert store.get_confidence(fact.id) == pytest.approx(engine.PRIOR)  # ...just inactive

    def test_evidence_active_at_exactly_the_window_boundary(self, engine: EvidenceEngine) -> None:
        store = engine.EvidenceStore()
        fact = _fact(value="boundary.app", mutability=FactMutability.EPHEMERAL, confidence=1.0)
        store.record(fact, _obs("sensor", "s1", T0))
        store.sweep(T0 + timedelta(days=1))
        # Active iff observed_at >= now − window: at exactly the boundary the
        # row still contributes.
        assert store.get_confidence(fact.id) > engine.PRIOR

    def test_mutable_evidence_survives_short_windows(self, engine: EvidenceEngine) -> None:
        store = engine.EvidenceStore()
        fact = _fact(value="home.address", mutability=FactMutability.MUTABLE, confidence=1.0)
        store.record(fact, _obs("user_confirm", "s1", T0))
        store.sweep(T0 + timedelta(days=1))
        assert store.get_confidence(fact.id) > engine.PRIOR  # 6-month window still active


class TestValueHashMatching:
    """Law: no evidence with a non-matching value_hash contributes.

    Negation → this test fails when stale evidence for an old value still moves
    the posterior after the fact's value changed (docs: 'posterior unchanged
    after value change').
    """

    def test_value_change_deactivates_old_evidence(self, engine: EvidenceEngine) -> None:
        store = engine.EvidenceStore()
        fact = _fact(value="likes.coffee", confidence=1.0)
        store.record(fact, _obs("user_confirm", "s1", T0))
        assert store.get_confidence(fact.id) > engine.PRIOR

        store.record(_fact(fact_id=fact.id, value="no.coffee", confidence=1.0))  # value change
        store.recompute(fact.id, now=T0 + timedelta(hours=1))
        assert store.get_confidence(fact.id) == pytest.approx(engine.PRIOR)

    def test_reverting_value_restores_old_evidence(self, engine: EvidenceEngine) -> None:
        store = engine.EvidenceStore()
        fact = _fact(value="likes.coffee", confidence=1.0)
        store.record(fact, _obs("user_confirm", "s1", T0))
        store.record(_fact(fact_id=fact.id, value="no.coffee", confidence=1.0))
        store.record(_fact(fact_id=fact.id, value="likes.coffee", confidence=1.0))  # revert
        store.sweep(T0 + timedelta(hours=1))
        assert store.get_confidence(fact.id) > engine.PRIOR  # old evidence active again

    def test_evidence_row_carries_the_observed_value_hash(self, engine: EvidenceEngine) -> None:
        store = engine.EvidenceStore()
        fact = _fact(value="likes.coffee", confidence=1.0)
        store.record(fact, _obs("user_confirm", "s1", T0))
        (row,) = store.evidence_for(fact.id)
        assert row.value_hash == sha256(b"likes.coffee").hexdigest()


class TestDedup:
    """Law: at most one evidence per (source_type, source_id, fact, value) per hour.

    Negation → this test fails when a duplicate observation within the hour
    creates a second row or moves the confidence twice (docs: 'count within
    window').
    """

    def test_duplicate_within_hour_counts_once(self, engine: EvidenceEngine) -> None:
        store = engine.EvidenceStore()
        fact = _fact(value="location.home", confidence=1.0)
        first = store.record(fact, _obs("user_confirm", "s1", T0))
        assert first is not None
        confidence = store.get_confidence(fact.id)

        duplicate = store.record(fact, _obs("user_confirm", "s1", T0 + timedelta(minutes=30)))
        assert duplicate is None  # discarded
        assert len(store.evidence_for(fact.id)) == 1
        assert store.get_confidence(fact.id) == confidence  # moved exactly once

    def test_duplicate_at_exactly_one_hour_is_still_discarded(self, engine: EvidenceEngine) -> None:
        store = engine.EvidenceStore()
        fact = _fact(value="location.home", confidence=1.0)
        store.record(fact, _obs("user_confirm", "s1", T0))
        confidence = store.get_confidence(fact.id)

        # The dedup window is inclusive: observed_at == now − 1 h still discards.
        duplicate = store.record(fact, _obs("user_confirm", "s1", T0 + timedelta(hours=1)))
        assert duplicate is None  # discarded at the exact boundary
        assert len(store.evidence_for(fact.id)) == 1
        assert store.get_confidence(fact.id) == confidence  # moved exactly once

    def test_observation_after_window_counts_again(self, engine: EvidenceEngine) -> None:
        store = engine.EvidenceStore()
        fact = _fact(value="location.home", confidence=1.0)
        store.record(fact, _obs("user_confirm", "s1", T0))
        store.record(fact, _obs("user_confirm", "s1", T0 + timedelta(hours=2)))
        assert len(store.evidence_for(fact.id)) == 2

    def test_dedup_key_includes_source_and_value(self, engine: EvidenceEngine) -> None:
        store = engine.EvidenceStore()
        fact = _fact(value="location.home", confidence=1.0)
        # Different source_id within the hour → distinct evidence.
        store.record(fact, _obs("user_confirm", "s1", T0))
        store.record(fact, _obs("user_confirm", "s2", T0 + timedelta(minutes=30)))
        # Different source_type within the hour → distinct evidence.
        store.record(fact, _obs("llm", "s1", T0 + timedelta(minutes=40)))
        # Different value within the hour → distinct evidence.
        store.record(
            _fact(fact_id=fact.id, value="location.work", confidence=1.0),
            _obs("user_confirm", "s1", T0 + timedelta(minutes=50)),
        )
        assert len(store.evidence_for(fact.id)) == 4


class TestConfidenceRange:
    """Law: confidence always lies in [0, 1].

    Negation → this test fails when any update produces a confidence outside
    [0, 1] — e.g. value-space interpolation overshoot (docs: ADR-0013).
    """

    def test_accumulating_support_saturates_within_range(self, engine: EvidenceEngine) -> None:
        store = engine.EvidenceStore()
        fact = _fact(value="location.home", confidence=1.0)
        previous = 0.0
        for hour in range(50):
            store.record(fact, _obs("user_confirm", f"s{hour}", T0 + timedelta(hours=hour)))
            confidence = store.get_confidence(fact.id)
            assert 0.0 <= confidence <= 1.0
            assert confidence >= previous  # monotone upward
            previous = confidence
        assert store.get_confidence(fact.id) > engine.PRIOR

    def test_accumulating_contradiction_stays_within_range(self, engine: EvidenceEngine) -> None:
        store = engine.EvidenceStore()
        fact = _fact(value="is.home", confidence=0.0)  # strength 0 → evidence against
        previous = 1.0
        for hour in range(50):
            store.record(fact, _obs("sensor", f"s{hour}", T0 + timedelta(hours=hour)))
            confidence = store.get_confidence(fact.id)
            assert 0.0 <= confidence <= 1.0
            assert confidence <= previous  # monotone downward
            previous = confidence
        assert store.get_confidence(fact.id) < engine.PRIOR


class TestUpdateRule:
    """The update rule is a pure, deterministic function (docs/evidence-system.md)."""

    def test_full_strength_applies_full_lr(self, engine: EvidenceEngine) -> None:
        for source_type, lr in engine.LR_BASE.items():
            shift = _logit(engine.update_rule(engine.PRIOR, 1.0, source_type)) - _logit(engine.PRIOR)
            assert shift == pytest.approx(math.log(lr))

    def test_half_strength_is_neutral(self, engine: EvidenceEngine) -> None:
        for source_type in engine.LR_BASE:
            assert engine.update_rule(engine.PRIOR, 0.5, source_type) == pytest.approx(engine.PRIOR)

    def test_zero_strength_weakens_by_full_lr(self, engine: EvidenceEngine) -> None:
        for source_type, lr in engine.LR_BASE.items():
            weakened = engine.update_rule(engine.PRIOR, 0.0, source_type)
            assert weakened < engine.PRIOR
            shift = _logit(weakened) - _logit(engine.PRIOR)
            assert shift == pytest.approx(-math.log(lr))

    def test_interpolation_is_linear_in_log_space(self, engine: EvidenceEngine) -> None:
        """strength interpolates between −LR and +LR in log space, not value space."""
        for source_type, lr in engine.LR_BASE.items():
            full_shift = math.log(lr)
            for strength, expected_shift in ((0.75, 0.5 * full_shift), (0.25, -0.5 * full_shift)):
                p = engine.update_rule(engine.PRIOR, strength, source_type)
                assert _logit(p) - _logit(engine.PRIOR) == pytest.approx(expected_shift)

    def test_boundaries_saturate_in_range(self, engine: EvidenceEngine) -> None:
        assert engine.update_rule(1.0, 1.0, "user_confirm") == 1.0
        assert engine.update_rule(0.0, 0.0, "sensor") == 0.0
        assert 0.0 <= engine.update_rule(0.999, 1.0, "user_confirm") <= 1.0
        assert 0.0 <= engine.update_rule(1e-9, 0.0, "llm") <= 1.0


class TestCalibrationConstants:
    """The calibration constants table in docs/evidence-system.md is pinned."""

    def test_lr_table(self, engine: EvidenceEngine) -> None:
        assert engine.LR_BASE == {"user_confirm": 20.0, "sensor": 10.0, "llm": 3.0}

    def test_prior_and_dedup_window(self, engine: EvidenceEngine) -> None:
        assert engine.PRIOR == 0.5
        assert engine.DEDUP_WINDOW == timedelta(hours=1)

    def test_mutability_windows(self, engine: EvidenceEngine) -> None:
        assert engine.WINDOW[FactMutability.EPHEMERAL] == timedelta(days=1)
        assert engine.WINDOW[FactMutability.MUTABLE] == timedelta(days=182)  # 6 months
        assert engine.WINDOW[FactMutability.SEMI_STATIC] == timedelta(days=1825)  # 5 years
        assert engine.WINDOW[FactMutability.STATIC] is None


class TestEvidenceSeam:
    """Law: the #83 seam clauses in the module docstring are enforced.

    These pin contract details no other test exercises: get_confidence must
    raise KeyError for unseen facts, and evidence_for must be ordered by
    observed_at.
    """

    def test_get_confidence_raises_key_error_for_unseen_fact(self, engine: EvidenceEngine) -> None:
        store = engine.EvidenceStore()
        with pytest.raises(KeyError):
            store.get_confidence(uuid4())

    def test_evidence_for_is_ordered_by_observed_at(self, engine: EvidenceEngine) -> None:
        store = engine.EvidenceStore()
        fact = _fact(value="location.home", confidence=1.0)
        # Recorded out of chronological order: the later observation first.
        store.record(fact, _obs("user_confirm", "s1", T0 + timedelta(hours=2)))
        store.record(fact, _obs("user_confirm", "s2", T0 + timedelta(hours=1)))
        rows = store.evidence_for(fact.id)
        assert [row.observed_at for row in rows] == [
            T0 + timedelta(hours=1),
            T0 + timedelta(hours=2),
        ]


def test_engine_module_exports_observation_and_evidence_value_objects() -> None:
    """The #83 module MUST export the Observation/Evidence value objects.

    The engine fixture never reads module.Observation/module.Evidence, so only
    this test pins that seam clause (docstring: "value objects with the fields
    used below").
    """
    module = pytest.importorskip(
        ENGINE_MODULE, reason=ENGINE_SKIP_REASON, exc_type=ModuleNotFoundError
    )
    observation = module.Observation(source_type="sensor", source_id="s1", observed_at=T0)
    assert observation.observed_at == T0
    evidence = module.Evidence(
        fact_id=uuid4(),
        source_type="sensor",
        source_id="s1",
        value_hash=sha256(b"likes.coffee").hexdigest(),
        strength=1.0,
        observed_at=T0,
    )
    assert evidence.value_hash == sha256(b"likes.coffee").hexdigest()


def test_suite_gate_skips_cleanly_until_engine_lands() -> None:
    """The contract tests are gated: clean skip today, live once #83 lands.

    Today ``cortex.memory.evidence_store`` does not exist and the gate raises a
    pytest.skip with an explicit '#83 not landed' reason — never a hard failure.
    When the engine lands the import resolves and this test passes through to
    the documented seam surface.
    """
    try:
        module = pytest.importorskip(
            ENGINE_MODULE, reason=ENGINE_SKIP_REASON, exc_type=ModuleNotFoundError
        )
    except pytest.skip.Exception as exc:
        assert ENGINE_SKIP_REASON in str(exc)
        return
    assert getattr(module, "EvidenceStore", None) is not None
