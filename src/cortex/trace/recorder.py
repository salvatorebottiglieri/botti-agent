"""TraceRecorder — tees a LoopEvent stream into ``loop_events`` storage
(issue #112 T2).

The recorder is a *consumer* of the AgentLoop async-generator stream, wired in
``ExecutionModule.stream_chat`` (the passthrough between the chat route and the
loop) — never inside the loop. It wraps the upstream stream, and for each event:

1. builds the event's self-describing ``to_dict()`` payload,
2. pseudonymizes PII-bearing string fields via the injected Pseudonymizer
   (one sidecar ``/analyze`` call per non-empty field), replacing them in the
   *stored copy* only — the original event object is re-yielded untouched,
3. persists the pseudonymized payload with the next monotonic ``seq``.

Fail-closed capture semantics (the caller's stream is NEVER altered or
interrupted):

* Pseudonymization failure (sidecar unreachable / non-2xx / malformed) —
  events are pseudonymized and persisted as they stream, so on the first
  failure the recorder latches degraded for the rest of the turn: the failing
  event and all later events of that turn are not stored, while events
  already persisted for the turn (its PII-free leading events) may remain in
  the store. Raw text is never stored: pseudonymization precedes every
  insert. Latching instead of retrying per event avoids repeated
  ``/analyze`` timeouts against a down sidecar and guarantees the stream is
  never interrupted (US6). A warning is logged.
* Persistence failure — only that event is skipped (DB errors are transient,
  not a PII risk); later events still attempt their insert. ``seq`` advances
  per attempt, so gaps are safe under ``UNIQUE(session_id, seq)``.
* ``max_seq`` failure at capture start — the starting seq cannot be derived
  safely, so nothing is stored for the turn.

``seq`` is monotonic per session across turns: a fresh session's first event
is ``seq`` 0, and later turns continue at the last persisted ``seq`` + 1
(never restarting at 0). v1 assumption: one active stream per session.

PII-bearing fields per spec: thinking.message, text.delta, done.message,
tool_done output/error, and ask_user.question (it echoes conversation PII).
tool_start fields and error.error are stored as-is per the spec field list.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterable, AsyncIterator
from typing import TYPE_CHECKING, Any
from uuid import UUID

from cortex.agentic.events import (
    AskUserEvent,
    LoopEvent,
    ResponseDoneEvent,
    TextDeltaEvent,
    ThinkingEvent,
    ToolResultEvent,
)

if TYPE_CHECKING:
    from cortex.trace.interfaces import TraceRepository
    from cortex.trace.pseudonymizer import Pseudonymizer

logger = logging.getLogger(__name__)

# Field names per event type that may echo conversation PII and are therefore
# pseudonymized (one sidecar call per non-empty field) BEFORE the insert.
_PII_FIELDS: dict[type[LoopEvent], tuple[str, ...]] = {
    ThinkingEvent: ("message",),
    TextDeltaEvent: ("delta",),
    ToolResultEvent: ("output", "error"),
    ResponseDoneEvent: ("message",),
    AskUserEvent: ("question",),
}


class TraceRecorder:
    """Consumes a LoopEvent stream, persisting pseudonymized events."""

    def __init__(self, repository: TraceRepository, pseudonymizer: Pseudonymizer):
        self._repository = repository
        self._pseudonymizer = pseudonymizer

    async def capture(
        self,
        session_id: UUID,
        events: AsyncIterable[LoopEvent],
    ) -> AsyncIterator[LoopEvent]:
        """Tee ``events`` into loop_events storage, re-yielding each unchanged.

        Args:
            session_id: The session the events belong to (seq continuity is
                per session across turns).
            events: The upstream AgentLoop event stream.

        Yields:
            Every event from ``events``, in order, exactly as received — even
            when its capture failed (see module docstring).
        """
        degraded = False
        # The first stored event of a turn is numbered max_seq + 1; a session
        # with no rows yet (max_seq -> None) starts from this -1 base, so its
        # first insert lands at seq 0. Never read once a failure below latches
        # ``degraded`` for the rest of the turn.
        seq = -1
        try:
            last_seq = await self._repository.max_seq(session_id)
        except Exception as exc:
            degraded = True
            logger.warning(
                "Trace capture skipped for session %s: cannot determine the "
                "last persisted seq (%r); no events stored for this turn",
                session_id,
                exc,
            )
        else:
            if last_seq is not None:
                seq = last_seq

        async for event in events:
            if not degraded:
                try:
                    payload = await self._pseudonymized_payload(event)
                except Exception as exc:
                    degraded = True
                    logger.warning(
                        "Trace capture: pseudonymization failed for a %s event "
                        "(session %s); the event and the remaining events of this "
                        "turn are not stored (%r)",
                        event.event_type,
                        session_id,
                        exc,
                    )
                else:
                    try:
                        seq += 1
                        await self._repository.insert_event(
                            session_id, seq, event.event_type, payload
                        )
                    except Exception as exc:
                        logger.warning(
                            "Trace capture: persistence failed for a %s event "
                            "(session %s); event skipped (%r)",
                            event.event_type,
                            session_id,
                            exc,
                        )
            yield event

    async def _pseudonymized_payload(self, event: LoopEvent) -> dict[str, Any]:
        """``event.to_dict()`` with PII-bearing string fields pseudonymized.

        Works on a fresh copy: the original event object is never mutated, so
        the stream delivered to the caller keeps its clear text.
        """
        payload = event.to_dict()
        for field in _PII_FIELDS.get(type(event), ()):
            value = payload.get(field)
            # Empty/None values carry nothing to pseudonymize — skip the call.
            if isinstance(value, str) and value:
                payload[field] = await self._pseudonymizer.anonymize(value)
        return payload
