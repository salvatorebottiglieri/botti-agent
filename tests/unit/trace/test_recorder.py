"""Tests for TraceRecorder — the LoopEvent-stream consumer (issue #112 T2).

The recorder contract under test:

* capture() tees a LoopEvent async stream into ``loop_events``: each event is
  persisted in seq order (starting after the session's last persisted seq, so
  seq stays monotonic ACROSS turns under UNIQUE(session_id, seq)) and then
  re-yielded *unchanged* — the stream delivered to the caller is never altered.
* PII-bearing string fields (thinking.message, text.delta, done.message,
  tool_done output/error, ask_user.question) are pseudonymized via the injected
  Pseudonymizer BEFORE the insert; tool_start fields and error.error stay as-is
  per the spec field list. Raw text is therefore never stored.
* Fail-closed: a pseudonymization failure (sidecar down) means the affected
  event and the remainder of the turn are not persisted; a persistence failure
  skips only that event; a max_seq failure at capture start stores nothing.
  In every failure mode every event is still yielded downstream.
"""

import json
import logging
from typing import Any
from uuid import UUID, uuid4

import pytest

from cortex.agentic.events import (
    AskUserEvent,
    ErrorEvent,
    LoopEvent,
    ResponseDoneEvent,
    TextDeltaEvent,
    ThinkingEvent,
    ToolResultEvent,
    ToolStartEvent,
)
from cortex.trace.models import TraceEvent
from cortex.trace.recorder import TraceRecorder
from tests.unit.trace.fakes import (
    CF_SEED,
    EMAIL_SEED,
    PII_SEEDS,
    FailingPseudonymizer,
    FakePseudonymizer,
    InMemoryTraceRepository,
)


def make_events(session_id: UUID) -> list[LoopEvent]:
    """A realistic full turn covering every streamed event type (I2)."""
    return [
        ThinkingEvent(session_id, message=f"Let me look up {EMAIL_SEED}."),
        ToolStartEvent(session_id, tool_name="web_search", tool_call_id="call_1"),
        ToolResultEvent(
            session_id,
            tool_name="web_search",
            tool_call_id="call_1",
            success=True,
            output=f"Found CF {CF_SEED}",
            error=None,
        ),
        ToolResultEvent(
            session_id,
            tool_name="shell",
            tool_call_id="call_2",
            success=False,
            output=None,
            error=f"could not reach {EMAIL_SEED}",
        ),
        TextDeltaEvent(session_id, delta=f"Contact {EMAIL_SEED} or {CF_SEED}."),
        ResponseDoneEvent(
            session_id,
            message=f"Done — {EMAIL_SEED} / {CF_SEED}.",
            tools_used=["web_search", "shell"],
            iterations=2,
        ),
        ErrorEvent(session_id, error="boom: no retries left", code="max_iterations"),
    ]


def payloads_json(events: list[TraceEvent]) -> str:
    """All stored payloads serialized as one JSON blob (any-level seed check)."""
    return json.dumps([e.payload for e in events])


class FlakyOnTextPseudonymizer(FakePseudonymizer):
    """Fails only for one exact input; the rest pass through to Fake semantics."""

    def __init__(self, failing_text: str, seeds: tuple[str, ...] = PII_SEEDS) -> None:
        super().__init__(seeds=seeds)
        self._failing_text = failing_text

    async def anonymize(self, text: str) -> str:
        if text == self._failing_text:
            self.calls.append(text)
            raise RuntimeError("transient sidecar blip")
        return await super().anonymize(text)


class FlakyInsertRepository(InMemoryTraceRepository):
    """Raises on insert for specific seqs (models transient DB failures)."""

    def __init__(self, failing_seqs: set[int]) -> None:
        super().__init__()
        self._failing_seqs = failing_seqs

    async def insert_event(
        self,
        session_id: UUID,
        seq: int,
        event_type: str,
        payload: dict[str, Any],
    ) -> TraceEvent:
        if seq in self._failing_seqs:
            raise RuntimeError("db connection lost")
        return await super().insert_event(session_id, seq, event_type, payload)


class BrokenMaxSeqRepository(InMemoryTraceRepository):
    """max_seq raises — models the trace store being unreachable."""

    async def max_seq(self, session_id: UUID) -> int | None:
        raise RuntimeError("db connection lost")


async def _drain(
    recorder: TraceRecorder, session_id: UUID, events: list[LoopEvent]
) -> list[LoopEvent]:
    async def _agen() -> Any:
        for event in events:
            yield event

    return [e async for e in recorder.capture(session_id, _agen())]


@pytest.fixture
def recorder() -> tuple[TraceRecorder, InMemoryTraceRepository, FakePseudonymizer]:
    repo = InMemoryTraceRepository()
    pseudonymizer = FakePseudonymizer()
    return TraceRecorder(repository=repo, pseudonymizer=pseudonymizer), repo, pseudonymizer


class TestTraceRecorderCapture:
    """Happy-path capture: seq order, coverage of all event types, PII scope."""

    @pytest.mark.asyncio
    async def test_full_turn_persisted_in_seq_order_with_every_event_type(self, recorder):
        """A full chat turn (thinking + tools + text + done + error) persists one
        row per event, in seq order, covering every streamed event type (I2)."""
        trace_recorder, repo, _ = recorder
        session_id = uuid4()
        scripted = make_events(session_id)

        seen = await _drain(trace_recorder, session_id, scripted)

        # The stream delivered to the caller is untouched: same objects, same order.
        assert seen == scripted
        assert all(a is b for a, b in zip(seen, scripted))

        rows = await repo.list_events(session_id)
        assert [r.event_type for r in rows] == [
            "thinking",
            "tool_start",
            "tool_done",
            "tool_done",
            "text",
            "done",
            "error",
        ]
        assert [r.seq for r in rows] == [0, 1, 2, 3, 4, 5, 6]
        assert rows  # anti-vacuity: rows exist, the assertions below are not vacuous

    @pytest.mark.asyncio
    async def test_stored_payloads_never_contain_seeded_pii(self, recorder):
        """I1: seeded PII (email / codice fiscale) in user/assistant/tool text
        never appears in clear in any stored payload — while rows DO exist and
        PII-bearing fields ARE pseudonymized to placeholders."""
        trace_recorder, repo, _ = recorder
        session_id = uuid4()
        scripted = make_events(session_id)

        await _drain(trace_recorder, session_id, scripted)

        rows = await repo.list_events(session_id)
        assert rows  # anti-vacuity: a capture that leaked by persisting nothing
        # would still pass the "no seed" check below — so require rows first.
        blob = payloads_json(rows)
        assert EMAIL_SEED not in blob
        assert CF_SEED not in blob

        by_type = {r.event_type: r.payload for r in rows}
        assert "[TAG_1]" in by_type["thinking"]["message"]  # thinking.message
        assert "[TAG_1]" in by_type["text"]["delta"]  # text.delta
        # done.message pseudonymized whole — one coherent placeholder text.
        assert "[TAG_1]" in by_type["done"]["message"]
        assert "[TAG_2]" in by_type["done"]["message"]
        # tool_done.output and tool_done.error each pseudonymized (one row each).
        tool_done = [r.payload for r in rows if r.event_type == "tool_done"]
        assert "[TAG_2]" in tool_done[0]["output"]
        assert "[TAG_1]" in tool_done[1]["error"]

    @pytest.mark.asyncio
    async def test_ask_user_question_pseudonymized_and_tool_start_error_raw(self, recorder):
        """ask_user.question is pseudonymized too (it echoes conversation PII);
        tool_start fields and error.error stay as-is per the spec field list."""
        trace_recorder, repo, pseudonymizer = recorder
        session_id = uuid4()
        scripted = [
            ToolStartEvent(session_id, tool_name="ask_user", tool_call_id="call_9"),
            AskUserEvent(session_id, question=f"Is {EMAIL_SEED} correct?"),
            ResponseDoneEvent(session_id, message=f"Is {EMAIL_SEED} correct?"),
            ErrorEvent(session_id, error="loop failed: no retries", code=None),
        ]

        await _drain(trace_recorder, session_id, scripted)

        rows = await repo.list_events(session_id)
        by_type = {r.event_type: r.payload for r in rows}
        assert EMAIL_SEED not in payloads_json(rows)
        assert CF_SEED not in payloads_json(rows)
        assert by_type["ask_user"]["question"] == "Is [TAG_1] correct?"
        # tool_start: no PII-bearing field per spec — stored verbatim.
        assert by_type["tool_start"]["tool_name"] == "ask_user"
        assert by_type["tool_start"]["tool_call_id"] == "call_9"
        # error.error: intentionally NOT pseudonymized per spec field list.
        assert by_type["error"]["error"] == "loop failed: no retries"
        assert by_type["error"]["code"] is None
        # One anonymize call per PII field: ask_user.question + done.message.
        assert len(pseudonymizer.calls) == 2

    @pytest.mark.asyncio
    async def test_capture_continues_seq_across_turns(self, recorder):
        """seq is monotonic per session ACROSS capture() calls: the second turn
        starts right after the highest seq the first turn persisted."""
        trace_recorder, repo, _ = recorder
        session_id = uuid4()
        first_turn = [
            ThinkingEvent(session_id, message="one"),
            TextDeltaEvent(session_id, delta="hello"),
        ]
        second_turn = [
            ThinkingEvent(session_id, message="two"),
            ResponseDoneEvent(session_id, message="done"),
        ]

        await _drain(trace_recorder, session_id, first_turn)
        await _drain(trace_recorder, session_id, second_turn)

        rows = await repo.list_events(session_id)
        # Turn one: seq 0-1. Turn two must NOT restart at 0 (UNIQUE(session_id, seq)).
        assert [r.seq for r in rows] == [0, 1, 2, 3]
        assert [r.event_type for r in rows] == [
            "thinking", "text", "thinking", "done",
        ]
        seqs = [r.seq for r in rows]
        assert len(seqs) == len(set(seqs))  # no duplicates

    @pytest.mark.asyncio
    async def test_capture_after_pre_seeded_session_continues_above_max(self, recorder):
        """A session with rows from a previous process/run continues above the
        persisted max (the recorder never assumes it starts at zero)."""
        trace_recorder, repo, _ = recorder
        session_id = uuid4()
        # Pre-existing rows (e.g. a prior capture before a restart).
        await repo.insert_event(session_id, 0, "thinking", {"message": "old"})
        await repo.insert_event(session_id, 1, "done", {"message": "old"})

        await _drain(
            trace_recorder,
            session_id,
            [TextDeltaEvent(session_id, delta="new turn")],
        )

        rows = await repo.list_events(session_id)
        assert [r.seq for r in rows] == [0, 1, 2]
        assert rows[-1].event_type == "text"

    @pytest.mark.asyncio
    async def test_empty_or_none_pii_fields_skip_the_sidecar(self, recorder):
        """Empty/None PII fields carry nothing to pseudonymize: no /analyze call
        is made and the empty value is stored as-is."""
        trace_recorder, repo, pseudonymizer = recorder
        session_id = uuid4()
        scripted = [
            TextDeltaEvent(session_id, delta=""),
            ToolResultEvent(
                session_id,
                tool_name="shell",
                tool_call_id="call_1",
                success=True,
                output="",
                error=None,
            ),
        ]

        await _drain(trace_recorder, session_id, scripted)

        assert pseudonymizer.calls == []
        rows = await repo.list_events(session_id)
        assert len(rows) == 2
        assert rows[0].payload["delta"] == ""
        assert rows[1].payload["output"] == ""
        assert rows[1].payload["error"] is None

    @pytest.mark.asyncio
    async def test_whitespace_only_delta_skips_sidecar_and_rest_of_turn_persists(self, recorder):
        """A whitespace-only text delta (e.g. ``" "`` between streamed tokens)
        carries no PII: no /analyze call is made for it, it is stored verbatim,
        and the later events of the turn still persist pseudonymized — the
        400/latch drop path is never triggered."""
        trace_recorder, repo, pseudonymizer = recorder
        session_id = uuid4()
        scripted = [
            TextDeltaEvent(session_id, delta="Hi "),
            TextDeltaEvent(session_id, delta=" "),  # whitespace-only token boundary
            TextDeltaEvent(session_id, delta=f"there {EMAIL_SEED}"),
            ResponseDoneEvent(session_id, message="done"),
        ]

        await _drain(trace_recorder, session_id, scripted)

        # One /analyze call per non-empty PII field; the whitespace-only delta
        # never reaches the pseudonymizer.
        assert pseudonymizer.calls == [
            "Hi ",
            f"there {EMAIL_SEED}",
            "done",
        ]
        assert " " not in pseudonymizer.calls

        # Every event of the turn persisted, in order — whitespace included.
        rows = await repo.list_events(session_id)
        assert [r.event_type for r in rows] == ["text", "text", "text", "done"]
        assert [r.seq for r in rows] == [0, 1, 2, 3]
        # The whitespace delta is stored verbatim; later PII still pseudonymized.
        assert [r.payload["delta"] for r in rows[:3]] == [
            "Hi ",
            " ",
            "there [TAG_1]",
        ]
        assert EMAIL_SEED not in payloads_json(rows)


class TestTraceRecorderFailClosed:
    """Every failure mode logs a warning and never alters the stream."""

    @pytest.mark.asyncio
    async def test_sidecar_down_stores_nothing_and_stream_completes(self, caplog):
        """Sidecar unreachable → the turn completes normally and NO rows are
        persisted for it (fail-closed); a warning is logged (US6)."""
        repo = InMemoryTraceRepository()
        failing = FailingPseudonymizer()
        trace_recorder = TraceRecorder(repository=repo, pseudonymizer=failing)
        session_id = uuid4()
        scripted = make_events(session_id)

        with caplog.at_level(logging.WARNING, logger="cortex.trace.recorder"):
            seen = await _drain(trace_recorder, session_id, scripted)

        # Chat stream never interrupted or altered.
        assert seen == scripted
        # Zero rows for the turn — the raw-thinking skip cascades to the rest.
        assert await repo.list_events(session_id) == []
        assert any("pseudonymization failed" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_one_failed_field_skips_its_event_and_turn_remainder(self, caplog):
        """Fail-closed per event: a failed pseudonymization drops the affected
        event (and, since the sidecar may stay down, the rest of the turn) while
        earlier events persist and every event still reaches the caller."""
        repo = InMemoryTraceRepository()
        flaky = FlakyOnTextPseudonymizer(failing_text="boom text")
        trace_recorder = TraceRecorder(repository=repo, pseudonymizer=flaky)
        session_id = uuid4()
        scripted = [
            ThinkingEvent(session_id, message="fine thinking"),
            TextDeltaEvent(session_id, delta="boom text"),  # only this call fails
            ResponseDoneEvent(session_id, message="never stored"),
        ]

        with caplog.at_level(logging.WARNING, logger="cortex.trace.recorder"):
            seen = await _drain(trace_recorder, session_id, scripted)

        assert seen == scripted  # stream untouched
        rows = await repo.list_events(session_id)
        assert [r.event_type for r in rows] == ["thinking"]
        assert any("pseudonymization failed" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_insert_failure_skips_only_that_event(self, caplog):
        """A persistence (DB) failure is transient, not a PII risk: only the
        affected event is skipped — later events still persist, with seq gaps
        safe under UNIQUE(session_id, seq)."""
        repo = FlakyInsertRepository(failing_seqs={1})
        pseudonymizer = FakePseudonymizer()
        trace_recorder = TraceRecorder(repository=repo, pseudonymizer=pseudonymizer)
        session_id = uuid4()
        scripted = [
            ThinkingEvent(session_id, message="think"),
            TextDeltaEvent(session_id, delta="will fail"),
            ResponseDoneEvent(session_id, message="persisted after the gap"),
        ]

        with caplog.at_level(logging.WARNING, logger="cortex.trace.recorder"):
            seen = await _drain(trace_recorder, session_id, scripted)

        assert seen == scripted
        rows = await repo.list_events(session_id)
        assert [r.seq for r in rows] == [0, 2]
        assert [r.event_type for r in rows] == ["thinking", "done"]
        assert any("persistence failed" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_max_seq_failure_stores_nothing_for_the_turn(self, caplog):
        """If the starting seq cannot be determined the recorder cannot safely
        number this turn: nothing is stored and the stream still flows."""
        repo = BrokenMaxSeqRepository()
        trace_recorder = TraceRecorder(repository=repo, pseudonymizer=FakePseudonymizer())
        session_id = uuid4()
        scripted = [
            ThinkingEvent(session_id, message="think"),
            TextDeltaEvent(session_id, delta="hi"),
        ]

        with caplog.at_level(logging.WARNING, logger="cortex.trace.recorder"):
            seen = await _drain(trace_recorder, session_id, scripted)

        assert seen == scripted
        assert await repo.list_events(session_id) == []
        assert any("cannot determine the last persisted seq" in r.message for r in caplog.records)
