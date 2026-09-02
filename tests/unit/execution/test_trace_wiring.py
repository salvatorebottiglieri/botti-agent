"""Wiring tests: ExecutionModule.stream_chat tees trace-enabled sessions into
the TraceRecorder (issue #112 T2).

The seam under test is ExecutionModule.stream_chat — the passthrough between
the chat route and AgentLoop. The recorder is injected explicitly (the module
never builds trace machinery itself): when ``trace_enabled`` is True each
yielded event is handed to the recorder (pseudonymize PII fields -> persist ->
yield); when False nothing is persisted and the stream is a transparent
passthrough. In every mode the stream delivered to the caller is never altered
or interrupted.
"""

import logging
from uuid import UUID, uuid4

import pytest

from cortex.agentic.events import (
    ErrorEvent,
    LoopEvent,
    ResponseDoneEvent,
    TextDeltaEvent,
    ThinkingEvent,
)
from cortex.agentic.models import MaxIterationsError
from cortex.execution.module import ExecutionModule
from cortex.trace.pseudonymizer import Pseudonymizer
from cortex.trace.recorder import TraceRecorder
from tests.unit.trace.fakes import (
    CF_SEED,
    EMAIL_SEED,
    FailingPseudonymizer,
    FakePseudonymizer,
    InMemoryTraceRepository,
)


class ScriptedLoop:
    """Async-generator stub loop mirroring the AgentLoop stream contract."""

    def __init__(
        self, events: list[LoopEvent], exc: Exception | None = None
    ) -> None:
        self._events = events
        self._exc = exc
        self.calls: list[tuple[UUID, str, int | None, bool]] = []

    async def stream_chat(
        self,
        session_id: UUID,
        user_message: str,
        *,
        max_iterations: int | None = None,
        stream: bool = False,
    ):
        self.calls.append((session_id, user_message, max_iterations, stream))
        for event in self._events:
            yield event
        if self._exc is not None:
            raise self._exc


def traced_module(
    events: list[LoopEvent],
    pseudonymizer: Pseudonymizer | None = None,
    loop_exc: Exception | None = None,
) -> tuple[ExecutionModule, ScriptedLoop, InMemoryTraceRepository, Pseudonymizer]:
    """ExecutionModule wired like production: recorder over a repo + sidecar."""
    loop = ScriptedLoop(events, exc=loop_exc)
    repo = InMemoryTraceRepository()
    pz = pseudonymizer or FakePseudonymizer()
    module = ExecutionModule(
        agent_loop=loop,
        trace_recorder=TraceRecorder(repository=repo, pseudonymizer=pz),
    )
    return module, loop, repo, pz


class TestStreamChatTraceWiring:
    """trace_enabled=True routes the stream through the recorder."""

    @pytest.mark.asyncio
    async def test_trace_enabled_stream_persists_pseudonymized_rows(self):
        """A traced turn: every event persisted (pseudonymized payloads, seq
        order) while the caller receives the original events unchanged."""
        session_id = uuid4()
        scripted = [
            ThinkingEvent(session_id, message=f"check {EMAIL_SEED}"),
            TextDeltaEvent(session_id, delta=f"mail {EMAIL_SEED}"),
            ResponseDoneEvent(session_id, message=f"ok {CF_SEED}", tools_used=[], iterations=1),
        ]
        module, loop, repo, pz = traced_module(scripted)

        seen = [e async for e in module.stream_chat(session_id, "hello", trace_enabled=True)]

        assert seen == scripted
        assert loop.calls == [(session_id, "hello", None, False)]
        rows = await repo.list_events(session_id)
        assert [r.seq for r in rows] == [0, 1, 2]
        assert [r.event_type for r in rows] == ["thinking", "text", "done"]
        # I1 anti-vacuity: rows exist AND no stored payload carries the seed.
        assert rows
        blob = "".join(str(r.payload) for r in rows)
        assert EMAIL_SEED not in blob and CF_SEED not in blob
        assert len(pz.calls) == 3  # one /analyze per PII-bearing field

    @pytest.mark.asyncio
    async def test_untraced_stream_persists_nothing(self):
        """Untraced session, identical turn -> zero rows; the recorder is never
        invoked (this assertion would fail if capture leaked)."""
        session_id = uuid4()
        scripted = [
            ThinkingEvent(session_id, message=f"check {EMAIL_SEED}"),
            TextDeltaEvent(session_id, delta=f"mail {EMAIL_SEED}"),
            ResponseDoneEvent(session_id, message=f"ok {CF_SEED}", tools_used=[], iterations=1),
        ]
        module, loop, repo, pz = traced_module(scripted)

        seen = [e async for e in module.stream_chat(session_id, "hello")]

        assert seen == scripted
        assert await repo.list_events(session_id) == []
        assert pz.calls == []  # recorder (and sidecar) never touched

    @pytest.mark.asyncio
    async def test_stream_flag_and_args_still_forwarded_when_traced(self):
        session_id = uuid4()
        scripted = [TextDeltaEvent(session_id, delta="hi")]
        module, loop, repo, _ = traced_module(scripted)

        _ = [e async for e in module.stream_chat(session_id, "hi", max_iterations=5, stream=True, trace_enabled=True)]

        assert loop.calls == [(session_id, "hi", 5, True)]

    @pytest.mark.asyncio
    async def test_error_contract_preserved_through_recorder(self):
        """A turn that raises still persists the events before the error
        (incl. the ErrorEvent itself) and the exception still propagates —
        capture never swallows or alters the stream (criterion 6)."""
        session_id = uuid4()
        scripted = [
            ThinkingEvent(session_id, message="thinking before the error"),
            TextDeltaEvent(session_id, delta="text before the error"),
            ErrorEvent(session_id, error="Max iterations exceeded", code="max_iterations"),
        ]
        module, loop, repo, _ = traced_module(scripted, loop_exc=MaxIterationsError(3))

        seen: list[LoopEvent] = []
        with pytest.raises(MaxIterationsError):
            async for event in module.stream_chat(session_id, "hello", trace_enabled=True):
                seen.append(event)

        assert [type(e).__name__ for e in seen] == [
            "ThinkingEvent", "TextDeltaEvent", "ErrorEvent",
        ]
        rows = await repo.list_events(session_id)
        assert [r.event_type for r in rows] == ["thinking", "text", "error"]
        assert [r.seq for r in rows] == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_trace_enabled_sidecar_down_never_interrupts_stream(self, caplog):
        """Sidecar unreachable: the stream completes normally and nothing is
        persisted for the turn (fail-closed, US6)."""
        session_id = uuid4()
        scripted = [
            ThinkingEvent(session_id, message="think"),
            TextDeltaEvent(session_id, delta="hi"),
            ResponseDoneEvent(session_id, message="done"),
        ]
        module, loop, repo, _ = traced_module(scripted, pseudonymizer=FailingPseudonymizer())

        with caplog.at_level(logging.WARNING, logger="cortex.trace.recorder"):
            seen = [e async for e in module.stream_chat(session_id, "hello", trace_enabled=True)]

        # Chat stream never interrupted or altered.
        assert seen == scripted
        # Zero rows for the turn — the raw-thinking skip cascades to the rest.
        assert await repo.list_events(session_id) == []
        assert any("pseudonymization failed" in r.message for r in caplog.records)
