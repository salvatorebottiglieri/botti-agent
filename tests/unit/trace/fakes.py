"""Shared in-memory fakes for trace capture tests (issue #112 T2).

Used by both ``tests/unit/trace/test_recorder.py`` (the TraceRecorder unit
tests) and ``tests/unit/execution/test_trace_wiring.py`` (the
ExecutionModule.stream_chat wiring tests) so the recorder contract is pinned
against one repository fake and one pseudonymizer fake.

* ``InMemoryTraceRepository`` — a TraceRepository whose ``max_seq`` derives
  from stored rows, modeling the real Postgres continuity across turns.
* ``FakePseudonymizer`` — deterministic sidecar stand-in: swaps each PII seed
  for a stable ``[TAG_N]`` placeholder and records every call.
* ``FailingPseudonymizer`` — raises on every call, modeling a down sidecar.

``EMAIL_SEED``/``CF_SEED`` are the PII seeds planted in scripted event text
(I1: email + codice fiscale): a stored payload containing either seed verbatim
means capture leaked clear PII.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from cortex.trace.interfaces import TraceRepository
from cortex.trace.models import TraceEvent
from cortex.trace.pseudonymizer import Pseudonymizer

EMAIL_SEED = "mario.rossi@example.com"
CF_SEED = "RSSMRA85H12F205Z"
PII_SEEDS = (EMAIL_SEED, CF_SEED)


class InMemoryTraceRepository(TraceRepository):
    """In-memory TraceRepository: max_seq derives from stored rows, so it
    models the real Postgres continuity across capture() calls."""

    def __init__(self) -> None:
        self.rows: list[TraceEvent] = []
        self._next_id = 1

    async def insert_event(
        self,
        session_id: UUID,
        seq: int,
        event_type: str,
        payload: dict[str, Any],
    ) -> TraceEvent:
        row = TraceEvent(
            id=self._next_id,
            session_id=session_id,
            seq=seq,
            event_type=event_type,
            payload=payload,
            created_at=datetime.now(UTC),
        )
        self._next_id += 1
        self.rows.append(row)
        return row

    async def list_events(self, session_id: UUID) -> list[TraceEvent]:
        return sorted(
            (r for r in self.rows if r.session_id == session_id), key=lambda r: r.seq
        )

    async def delete_older_than(self, cutoff: datetime) -> int:
        kept = [r for r in self.rows if r.created_at >= cutoff]
        deleted = len(self.rows) - len(kept)
        self.rows = kept
        return deleted

    async def max_seq(self, session_id: UUID) -> int | None:
        seqs = [r.seq for r in self.rows if r.session_id == session_id]
        return max(seqs) if seqs else None


class FakePseudonymizer(Pseudonymizer):
    """Deterministic sidecar stand-in: swaps each PII seed for a stable
    [TAG_N] placeholder; numbering restarts per anonymize() call (same
    contract shape as the pinned rizzo sidecar)."""

    def __init__(self, seeds: tuple[str, ...] = PII_SEEDS) -> None:
        self._seeds = seeds
        self.calls: list[str] = []

    async def anonymize(self, text: str) -> str:
        self.calls.append(text)
        anonymized = text
        for index, seed in enumerate(self._seeds, start=1):
            anonymized = anonymized.replace(seed, f"[TAG_{index}]")
        return anonymized


class FailingPseudonymizer(Pseudonymizer):
    """Raises on every anonymize() call — models an unreachable sidecar."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error or RuntimeError("sidecar unreachable")
        self.calls: list[str] = []

    async def anonymize(self, text: str) -> str:
        self.calls.append(text)
        raise self._error
