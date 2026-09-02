"""Trace audit — reconstruct a stored LoopEvent trace and run the Trajectory
Judge over it for diagnosis (issue #113 T3).

The read-side counterpart of the T2 capture pipeline. ``audit_session``

1. reconstructs the typed LoopEvent sequence from the stored pseudonymized
   ``loop_events`` payloads (seq order; ``event_type`` dispatch over all
   seven event classes, incl. usage/latency round-trip),
2. describes the session to the judge with its FIRST user turn — fetched via
   the injected SessionRepository and pseudonymized through the same
   Pseudonymizer sidecar the recorder uses,
3. re-pseudonymizes stored-raw ``ErrorEvent.error`` text through that same
   Pseudonymizer — no real PII reaches the judge: stored free-text fields are
   placeholders from capture, while ``ErrorEvent.error`` (stored raw by
   design) is pseudonymized at audit time,
4. runs ``TrajectoryJudge.judge_with_order_swap`` with synthetic metadata.

Never a pass/fail verdict: the annotated goal state remains the only oracle
(ADR-0017), so the judge output is a per-dimension diagnosis + partial
credit. Traces are never fed back into prompts/context/training/learning:
the context builder has no dependency on the trace repository (structural
pin asserted in ``tests/unit/trace/test_audit.py``).

Fail-closed semantics:

* the pseudonymizer failing / unreachable → explicit :class:`TraceAuditError`
  and NO judge call,
* a stored payload that cannot be reconstructed (unknown ``event_type`` or a
  schema drift between capture and audit) → explicit :class:`TraceAuditError`,
* a session with no captured events or no user message cannot be described
  meaningfully → explicit :class:`TraceAuditError`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

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
from cortex.llm.models import UsageStats
from cortex.sessions.models import MessageRole

if TYPE_CHECKING:
    from cortex.config.models import Settings
    from cortex.eval.judge import JudgeVerdict, TrajectoryJudge
    from cortex.sessions.interfaces import SessionRepository
    from cortex.sessions.models import Message
    from cortex.trace.interfaces import TraceRepository
    from cortex.trace.pseudonymizer import Pseudonymizer

__all__ = ["TraceAuditError", "audit_session", "reconstruct_event"]

#: Dispatch of the stored wire ``event_type`` → typed event class: the seven
#: event classes the recorder captures (ADR-0002's one vocabulary, no mapping
#: table).
_EVENT_CLASSES: dict[str, type[LoopEvent]] = {
    ThinkingEvent.event_type: ThinkingEvent,
    TextDeltaEvent.event_type: TextDeltaEvent,
    ToolStartEvent.event_type: ToolStartEvent,
    ToolResultEvent.event_type: ToolResultEvent,
    ResponseDoneEvent.event_type: ResponseDoneEvent,
    AskUserEvent.event_type: AskUserEvent,
    ErrorEvent.event_type: ErrorEvent,
}

#: get_messages window size when walking a session's full conversation (the
#: SessionRepository default limit).
_MESSAGE_FETCH_LIMIT = 50

#: Synthetic goal summary for unannotated runtime sessions (ADR-0017: the
#: judge is never a pass/fail oracle, so the summary states the audit is
#: diagnosis-only).
_GOAL_SUMMARY = "unannotated runtime session — diagnosis only"


class TraceAuditError(RuntimeError):
    """The trace audit failed closed before producing a verdict.

    Raised when the stored trace cannot be reconstructed, the session has no
    user message to describe, or the pseudonymizer is unreachable — in every
    case NO judge call is made and no verdict is produced.
    """


def reconstruct_event(payload: dict[str, Any]) -> LoopEvent:
    """Reconstruct a typed LoopEvent from one stored self-describing payload.

    The payload is the event's own ``to_dict()``::

        {"event_type": "done", "session_id": "<uuid>", ...fields...}

    Dispatch on ``event_type``, parse ``session_id`` back to a :class:`UUID`
    and — for the ``done`` event — the ``usage`` dict back to a
    :class:`~cortex.llm.models.UsageStats`, so re-serializing the event with
    ``to_dict()`` round-trips the payload exactly (incl. usage/latency).

    Raises:
        TraceAuditError: Unknown ``event_type``, or a payload that no longer
            matches the current event schema (e.g. a store written by a
            different code version). The audit fails closed rather than
            judging a corrupted or partial trace.
    """
    if not isinstance(payload, dict):
        raise TraceAuditError(
            f"stored trace payload must be a mapping, got {type(payload).__name__}"
        )
    event_type = payload.get("event_type")
    if not isinstance(event_type, str):
        raise TraceAuditError(
            f"stored trace payload has malformed event_type {event_type!r}"
        )
    event_cls = _EVENT_CLASSES.get(event_type)
    if event_cls is None:
        raise TraceAuditError(
            f"stored trace payload has unknown event_type {event_type!r}"
        )
    fields = dict(payload)
    fields.pop("event_type", None)
    try:
        fields["session_id"] = UUID(str(fields["session_id"]))
        if event_cls is ResponseDoneEvent and fields.get("usage") is not None:
            fields["usage"] = UsageStats(**fields["usage"])
        return event_cls(**fields)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise TraceAuditError(
            f"stored {event_type!r} payload cannot be reconstructed "
            f"(schema drift between capture and audit?): {exc}"
        ) from exc


async def audit_session(
    session_id: UUID,
    *,
    session_repo: SessionRepository,
    trace_repo: TraceRepository,
    judge: TrajectoryJudge | None = None,
    pseudonymizer: Pseudonymizer | None = None,
) -> JudgeVerdict:
    """Audit one session's captured trace with the Trajectory Judge.

    Reconstructs the session's LoopEvent sequence from the stored
    pseudonymized payloads and runs ``TrajectoryJudge.judge_with_order_swap``
    with synthetic metadata: ``task_name="session-<id>"``, the session's
    first user turn (pseudonymized) as ``task_description`` (stored-raw
    ``ErrorEvent.error`` text is pseudonymized too), and
    ``goal_summary="unannotated runtime session — diagnosis only"``. The
    verdict is per-dimension diagnosis + partial credit — never pass/fail.

    Args:
        session_id: The session to audit.
        session_repo: Session store; ``get_messages`` supplies the session
            conversation, whose FIRST user turn is described to the judge
            (never raw — it is pseudonymized first).
        trace_repo: Loop-trace store; ``list_events`` supplies the captured
            pseudonymized payloads in seq order.
        judge: Trajectory Judge to run; when None, one is built from settings
            (``build_judge_client(get_settings())`` + :class:`TrajectoryJudge`).
        pseudonymizer: Sidecar pseudonymizer for the first user turn and for
            stored-raw ``ErrorEvent.error`` text (capture stores it as-is by
            design); when None, the same rizzo sidecar the recorder uses is
            built from settings. Any failure raises and no judge call happens
            (fail closed, I5).

    Returns:
        The judge's :class:`~cortex.eval.judge.JudgeVerdict`: per-dimension
        scores/diagnoses plus normalized partial credit, with the
        order-swap consistency semantics of the eval harness.

    Raises:
        TraceAuditError: The stored trace cannot be reconstructed, the
            session has no captured events or no user message, or the
            pseudonymizer failed — no judge call is made in any of these.
    """
    rows = await trace_repo.list_events(session_id)
    if not rows:
        raise TraceAuditError(
            f"session {session_id} has no captured loop events; "
            "trace capture may have been disabled or degraded"
        )
    events = [reconstruct_event(row.payload) for row in rows]

    conversation = await _session_messages_oldest_first(session_repo, session_id)
    first_turn = _first_user_message_content(conversation)
    if first_turn is None:
        raise TraceAuditError(
            f"session {session_id} has no user message to describe the task"
        )

    if pseudonymizer is None or judge is None:
        from cortex.config.loader import get_settings

        settings = get_settings()
    if pseudonymizer is None:
        pseudonymizer = _default_pseudonymizer(settings)
    try:
        task_description = await pseudonymizer.anonymize(first_turn)
        await _pseudonymize_error_events(events, pseudonymizer)
    except Exception as exc:  # noqa: BLE001 - fail closed on ANY sidecar failure
        raise TraceAuditError(
            f"trace audit failed closed for session {session_id}: cannot "
            f"pseudonymize session text before judging ({exc!r}); no judge "
            "call made"
        ) from exc

    if judge is None:
        judge = _default_judge(settings)
    return await judge.judge_with_order_swap(
        events,
        task_name=f"session-{session_id}",
        task_description=task_description,
        goal_summary=_GOAL_SUMMARY,
    )


async def _session_messages_oldest_first(
    session_repo: SessionRepository, session_id: UUID
) -> list[Message]:
    """Every session message, oldest first.

    ``get_messages`` returns the newest ``limit`` messages (oldest-first
    inside that window — the ordering followed by every in-repo
    implementation and fake), so when a window is completely full we page
    backward with ``before`` cursors until the session start. A short
    session costs exactly one call.
    """
    batch = await session_repo.get_messages(session_id)
    if len(batch) < _MESSAGE_FETCH_LIMIT:
        return list(batch)
    batches = [batch]
    while True:
        cursor = batches[-1][0].created_at
        older = await session_repo.get_messages(session_id, before=cursor)
        if not older:
            break
        batches.append(older)
        if len(older) < _MESSAGE_FETCH_LIMIT:
            break
    return [message for older_batch in reversed(batches) for message in older_batch]


def _first_user_message_content(conversation: list[Message]) -> str | None:
    """The text of the conversation's FIRST USER-role message (oldest first)."""
    for message in conversation:
        if message.role == MessageRole.USER:
            return message.content
    return None


async def _pseudonymize_error_events(
    events: list[LoopEvent], pseudonymizer: Pseudonymizer
) -> None:
    """Re-pseudonymize stored-raw ``ErrorEvent.error`` text in place.

    Capture stores ``error.error`` as-is (T2 field list), so an error echoing
    user input would otherwise carry real PII into the judge transcript. One
    sidecar call per error event with a non-empty error.
    """
    for event in events:
        if isinstance(event, ErrorEvent) and event.error:
            event.error = await pseudonymizer.anonymize(event.error)


def _default_judge(settings: Settings) -> TrajectoryJudge:
    """Build the production judge: the eval judge client from settings."""
    from cortex.eval.judge import TrajectoryJudge, build_judge_client

    return TrajectoryJudge(build_judge_client(settings))


def _default_pseudonymizer(settings: Settings) -> Pseudonymizer:
    """Build the production pseudonymizer: the same rizzo sidecar as capture."""
    from cortex.trace.pseudonymizer import RizzoPseudonymizer

    return RizzoPseudonymizer(
        settings.trace_sidecar_url, timeout=settings.trace_sidecar_timeout_s
    )
