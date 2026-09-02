"""Tests for the trace audit (issue #113 T3).

``audit_session`` reconstructs a session's stored LoopEvent trace and runs
the Trajectory Judge over it for per-dimension diagnosis + partial credit —
never a pass/fail verdict (ADR-0017). Real PII never reaches the judge, and
a down pseudonymizer fails the audit closed with no judge call. The context
builder's independence from the trace repository is pinned structurally.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from dataclasses import fields
from pathlib import Path
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
from cortex.config.models import Settings
from cortex.eval.judge import (
    DEFAULT_DIMENSION_ORDER,
    JudgeVerdict,
    TrajectoryJudge,
)
from cortex.llm.models import ChatMessage, ChatResult, Role, UsageStats
from cortex.sessions.models import MessageRole
from cortex.trace import audit as trace_audit
from cortex.trace.audit import TraceAuditError, audit_session, reconstruct_event
from cortex.trace.recorder import TraceRecorder
from tests.eval.fakes import ScriptedLLMClient
from tests.unit.trace.fakes import (
    CF_SEED,
    EMAIL_SEED,
    FailingPseudonymizer,
    FakePseudonymizer,
    InMemorySessionRepository,
    InMemoryTraceRepository,
)

#: Names that would expose a pass/fail verdict — I3 pins none ever appears.
_PASS_FAIL_NAMES = frozenset(
    {"pass", "fail", "passed", "failed", "passes", "fails", "pass_fail", "is_pass"}
)


def _chat_result(text: str) -> ChatResult:
    return ChatResult(message=ChatMessage(role=Role.ASSISTANT, content=text))


def _judge_form(scores: dict[str, int], diagnosis: str) -> str:
    """A form-fill judge response (scores + a shared per-dimension diagnosis)."""
    lines = [
        f"{dim.value}: {scores[dim.value]}\n{dim.value}_diagnosis: {diagnosis}"
        for dim in DEFAULT_DIMENSION_ORDER
    ]
    return "\n".join(lines)


def _consistent_judge_client() -> ScriptedLLMClient:
    """Two identical order-swap passes → a consistent verdict."""
    form = _judge_form(
        {"tool_selection": 4, "arguments": 3, "efficiency": 2, "policy": 5},
        "dimension diagnosis",
    )
    return ScriptedLLMClient([_chat_result(form), _chat_result(form)])


def _inconsistent_judge_client() -> ScriptedLLMClient:
    """Order-swap passes disagree beyond tolerance → escalated verdict."""
    high = _judge_form({dim.value: 5 for dim in DEFAULT_DIMENSION_ORDER}, "great")
    low = _judge_form({dim.value: 1 for dim in DEFAULT_DIMENSION_ORDER}, "poor")
    return ScriptedLLMClient([_chat_result(high), _chat_result(low)])


def _judge_client_payloads(client: ScriptedLLMClient) -> str:
    """All text the judge client ever received, flattened."""
    return "\n".join(
        message.content or ""
        for call in client.calls
        for message in call
        if message.content
    )


async def _seed_conversation(
    session_repo: InMemorySessionRepository, session_id: UUID, turns: list[str]
) -> None:
    """Append USER turns interleaved with assistant acks, oldest first."""
    for content in turns:
        await session_repo.add_message(session_id, MessageRole.USER, content)
        await session_repo.add_message(session_id, MessageRole.ASSISTANT, "ok.")


async def _capture_turn(events: list[LoopEvent]) -> InMemoryTraceRepository:
    """Store a turn through the real capture pipeline (pseudonymized)."""
    repo = InMemoryTraceRepository()
    recorder = TraceRecorder(repository=repo, pseudonymizer=FakePseudonymizer())
    session_id = events[0].session_id

    async def _agen() -> Any:
        for event in events:
            yield event

    drained = [e async for e in recorder.capture(session_id, _agen())]
    assert drained == events  # capture never alters the stream
    return repo


def _pii_echo_events(session_id: UUID) -> list[LoopEvent]:
    """A full realistic turn whose text echoes both PII seeds (I1 shape)."""
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
            execution_time_ms=12.5,
        ),
        ToolResultEvent(
            session_id,
            tool_name="shell",
            tool_call_id="call_2",
            success=False,
            output=None,
            error=f"could not reach {EMAIL_SEED}",
            execution_time_ms=3.25,
        ),
        TextDeltaEvent(session_id, delta=f"Contact {EMAIL_SEED} or {CF_SEED}."),
        ResponseDoneEvent(
            session_id,
            message=f"Done — {EMAIL_SEED} / {CF_SEED}.",
            tools_used=["web_search", "shell"],
            iterations=2,
            usage=UsageStats(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            latency_ms=804.25,
        ),
        AskUserEvent(session_id, question=f"confirm {EMAIL_SEED}?", options=["yes", "no"]),
        ErrorEvent(session_id, error="boom: no retries left", code="max_iterations"),
    ]


# ────────────────────────────────────────────────────────────────────────────
# Reconstruction factory (round-trip: payload → typed event → payload)
# ────────────────────────────────────────────────────────────────────────────


class TestReconstructEvent:
    """A stored to_dict() payload round-trips through the factory."""

    @pytest.mark.parametrize(
        ("event", "expected_type"),
        [
            (ThinkingEvent(uuid4(), message="think"), ThinkingEvent),
            (TextDeltaEvent(uuid4(), delta="text"), TextDeltaEvent),
            (
                ToolStartEvent(uuid4(), tool_name="shell", tool_call_id="call_1"),
                ToolStartEvent,
            ),
            (
                ToolResultEvent(
                    uuid4(),
                    tool_name="shell",
                    tool_call_id="call_1",
                    success=True,
                    output="ok",
                ),
                ToolResultEvent,
            ),
            (
                ResponseDoneEvent(
                    uuid4(),
                    message="done",
                    tools_used=["shell"],
                    iterations=3,
                    latency_ms=42.5,
                ),
                ResponseDoneEvent,
            ),
            (
                AskUserEvent(uuid4(), question="pick?", options=["a", "b"]),
                AskUserEvent,
            ),
            (ErrorEvent(uuid4(), error="boom", code="max_iterations"), ErrorEvent),
        ],
    )
    def test_event_type_round_trip(
        self, event: LoopEvent, expected_type: type[LoopEvent]
    ) -> None:
        payload = event.to_dict()
        rebuilt = reconstruct_event(payload)
        assert type(rebuilt) is expected_type
        assert rebuilt.session_id == event.session_id
        assert rebuilt.to_dict() == payload  # event_type, order and fields intact

    def test_usage_and_latency_round_trip(self) -> None:
        event = ResponseDoneEvent(
            uuid4(),
            message="done",
            tools_used=["shell", "web_search"],
            iterations=4,
            usage=UsageStats(prompt_tokens=101, completion_tokens=23, total_tokens=124),
            latency_ms=812.75,
        )
        rebuilt = reconstruct_event(event.to_dict())
        assert isinstance(rebuilt, ResponseDoneEvent)
        assert rebuilt.usage == UsageStats(
            prompt_tokens=101, completion_tokens=23, total_tokens=124
        )
        assert rebuilt.latency_ms == 812.75
        assert rebuilt.to_dict() == event.to_dict()

    def test_tool_result_timing_round_trip(self) -> None:
        event = ToolResultEvent(
            uuid4(),
            tool_name="shell",
            tool_call_id="call_9",
            success=False,
            output=None,
            error="boom",
            execution_time_ms=0.125,
        )
        rebuilt = reconstruct_event(event.to_dict())
        assert isinstance(rebuilt, ToolResultEvent)
        assert rebuilt.success is False
        assert rebuilt.execution_time_ms == 0.125
        assert rebuilt.to_dict() == event.to_dict()

    def test_usage_none_stays_none(self) -> None:
        payload = ResponseDoneEvent(uuid4(), message="no usage").to_dict()
        assert payload["usage"] is None
        rebuilt = reconstruct_event(payload)
        assert isinstance(rebuilt, ResponseDoneEvent)
        assert rebuilt.usage is None

    def test_unknown_event_type_fails_closed(self) -> None:
        with pytest.raises(TraceAuditError, match="unknown event_type"):
            reconstruct_event({"event_type": "telepathy", "session_id": str(uuid4())})

    def test_missing_session_id_fails_closed(self) -> None:
        with pytest.raises(TraceAuditError, match="cannot be reconstructed"):
            reconstruct_event({"event_type": "thinking", "message": "hi"})

    def test_schema_drift_fails_closed(self) -> None:
        payload = {"event_type": "thinking", "session_id": str(uuid4())}
        with pytest.raises(TraceAuditError, match="cannot be reconstructed"):
            reconstruct_event(payload)

    async def test_captured_sequence_reconstructs_in_seq_order(self) -> None:
        session_id = uuid4()
        events = _pii_echo_events(session_id)
        rows = (await _capture_turn(events)).rows
        assert [r.event_type for r in rows] == [
            ThinkingEvent.event_type,
            ToolStartEvent.event_type,
            ToolResultEvent.event_type,
            ToolResultEvent.event_type,
            TextDeltaEvent.event_type,
            ResponseDoneEvent.event_type,
            AskUserEvent.event_type,
            ErrorEvent.event_type,
        ]
        rebuilt = [reconstruct_event(row.payload) for row in rows]
        # Stored payloads are the pseudonymized capture copy; reconstruction
        # must round-trip each row's payload verbatim (type + order preserved).
        assert [type(e) for e in rebuilt] == [type(e) for e in events]
        assert all(e.to_dict() == row.payload for e, row in zip(rebuilt, rows))


# ────────────────────────────────────────────────────────────────────────────
# audit_session: verdict shape, order-swap semantics, synthetic metadata
# ────────────────────────────────────────────────────────────────────────────


class TestAuditSession:
    """audit_session returns the eval judge's diagnosis verdict over a
    captured pseudonymized transcript."""

    async def test_verdict_carries_diagnoses_and_partial_credit(self) -> None:
        session_id = uuid4()
        session_repo = InMemorySessionRepository()
        await _seed_conversation(
            session_repo,
            session_id,
            [f"Send the report to {EMAIL_SEED} (cf {CF_SEED}).", "Thanks!"],
        )
        trace_repo = await _capture_turn(_pii_echo_events(session_id))
        client = _consistent_judge_client()
        verdict = await audit_session(
            session_id,
            session_repo=session_repo,
            trace_repo=trace_repo,
            judge=TrajectoryJudge(client),
            pseudonymizer=FakePseudonymizer(),
        )

        assert type(verdict) is JudgeVerdict
        assert set(verdict.scores) == set(DEFAULT_DIMENSION_ORDER)
        assert all(1 <= verdict.scores[dim] <= 5 for dim in DEFAULT_DIMENSION_ORDER)
        # (4+3+2+5)/4 = 3.5 Likert → (3.5 - 1)/4 = 0.625 partial credit.
        assert verdict.partial_credit == pytest.approx(0.625)
        assert verdict.consistent is True
        assert verdict.samples == 2
        assert verdict.rubric_version == "1"
        assert verdict.diagnoses == {dim: "dimension diagnosis" for dim in DEFAULT_DIMENSION_ORDER}
        assert "dimension diagnosis" in verdict.diagnosis

        # Synthetic metadata reached the judge verbatim, pseudonymized.
        payload = _judge_client_payloads(client)
        assert f"Task: session-{session_id}" in payload
        assert "Description: Send the report to [TAG_1] (cf [TAG_2])." in payload
        assert "Goal summary: unannotated runtime session — diagnosis only" in payload

    async def test_inconsistent_order_swap_escalates_never_passes(self) -> None:
        """Order-swap consistency semantics as in eval: a per-dimension gap
        beyond the tolerance yields consistent=False and no credit, with the
        escalate-to-human diagnosis — still not a pass/fail."""
        session_id = uuid4()
        session_repo = InMemorySessionRepository()
        await _seed_conversation(session_repo, session_id, ["Do the thing."])
        trace_repo = await _capture_turn(_pii_echo_events(session_id))
        client = _inconsistent_judge_client()
        verdict = await audit_session(
            session_id,
            session_repo=session_repo,
            trace_repo=trace_repo,
            judge=TrajectoryJudge(client),
            pseudonymizer=FakePseudonymizer(),
        )
        assert type(verdict) is JudgeVerdict
        assert verdict.consistent is False
        assert verdict.partial_credit is None
        assert verdict.scores == {}


# ────────────────────────────────────────────────────────────────────────────
# I3 anti-vacuity: the verdict exposes no pass/fail field
# ────────────────────────────────────────────────────────────────────────────


class TestNoPassFailVerdict:
    """I3: audit output is diagnosis + partial credit — never pass/fail.
    These assertions fail the moment a pass/fail field is added."""

    def test_eval_verdict_dataclass_has_no_pass_fail_field(self) -> None:
        names = {f.name for f in fields(JudgeVerdict)}
        assert not names & _PASS_FAIL_NAMES

    async def test_audit_verdict_object_exposes_no_pass_fail(self) -> None:
        session_id = uuid4()
        session_repo = InMemorySessionRepository()
        await _seed_conversation(session_repo, session_id, ["Do the thing."])
        trace_repo = await _capture_turn(_pii_echo_events(session_id))
        verdict = await audit_session(
            session_id,
            session_repo=session_repo,
            trace_repo=trace_repo,
            judge=TrajectoryJudge(_consistent_judge_client()),
            pseudonymizer=FakePseudonymizer(),
        )
        for name in _PASS_FAIL_NAMES:
            assert not hasattr(verdict, name), f"verdict exposes forbidden {name!r}"
        # The audit return type IS the eval verdict type, so any pass/fail
        # addition to the judge contract is caught above, not hidden in a
        # trace-local clone.
        assert type(verdict) is JudgeVerdict


# ────────────────────────────────────────────────────────────────────────────
# I5 anti-vacuity: PII never reaches the judge
# ────────────────────────────────────────────────────────────────────────────


class TestPrivacy:
    """I5: with a seeded PII value in the source conversation or in a
    stored-raw ``ErrorEvent.error``, judge client payloads contain only
    placeholder text — the assertions fail if a real value appears in any
    judge payload."""

    async def test_seeded_pii_never_reaches_judge_payloads(self) -> None:
        session_id = uuid4()
        session_repo = InMemorySessionRepository()
        first_turn = f"Send the summary to {EMAIL_SEED} (cf {CF_SEED}), please."
        await _seed_conversation(session_repo, session_id, [first_turn, "Thanks!"])
        trace_repo = await _capture_turn(_pii_echo_events(session_id))
        client = _consistent_judge_client()
        verdict = await audit_session(
            session_id,
            session_repo=session_repo,
            trace_repo=trace_repo,
            judge=TrajectoryJudge(client),
            pseudonymizer=FakePseudonymizer(),
        )
        assert verdict.partial_credit is not None  # the judge actually ran

        # The judge ran the two order-swap passes.
        assert len(client.calls) == 2
        for call in client.calls:
            for message in call:
                content = message.content or ""
                assert EMAIL_SEED not in content
                assert CF_SEED not in content
        # Placeholders (not a vacuous absence): the pseudonymized description
        # and transcript carry [TAG_N] markers in the judge payloads.
        payload = _judge_client_payloads(client)
        assert "[TAG_1]" in payload and "[TAG_2]" in payload

    async def test_first_user_turn_only_is_described(self) -> None:
        """The judge description is the FIRST user turn — a seed in a later
        turn never appears in the payloads."""
        session_id = uuid4()
        session_repo = InMemorySessionRepository()
        await _seed_conversation(session_repo, session_id, ["start here"])
        await session_repo.add_message(session_id, MessageRole.USER, f"later: {EMAIL_SEED}")
        trace_repo = await _capture_turn(_pii_echo_events(session_id))
        client = _consistent_judge_client()
        await audit_session(
            session_id,
            session_repo=session_repo,
            trace_repo=trace_repo,
            judge=TrajectoryJudge(client),
            pseudonymizer=FakePseudonymizer(),
        )
        payload = _judge_client_payloads(client)
        assert "Description: start here" in payload
        assert EMAIL_SEED not in payload

    async def test_first_turn_recovered_when_conversation_exceeds_window(self) -> None:
        """Paging: the first user turn is older than get_messages' newest-50
        window, so audit_session must page back to the session start."""
        session_id = uuid4()
        session_repo = InMemorySessionRepository()
        first_turn = f"original task for {EMAIL_SEED}"
        await session_repo.add_message(session_id, MessageRole.USER, first_turn)
        await session_repo.add_message(session_id, MessageRole.ASSISTANT, "ok.")
        for index in range(30):
            await session_repo.add_message(session_id, MessageRole.USER, f"filler {index}")
            await session_repo.add_message(session_id, MessageRole.ASSISTANT, "ok.")
        # 62 messages → the newest-50 window no longer contains the first turn.
        window = await session_repo.get_messages(session_id)
        assert len(window) == 50
        assert first_turn not in {m.content for m in window}

        trace_repo = await _capture_turn(_pii_echo_events(session_id))
        client = _consistent_judge_client()
        await audit_session(
            session_id,
            session_repo=session_repo,
            trace_repo=trace_repo,
            judge=TrajectoryJudge(client),
            pseudonymizer=FakePseudonymizer(),
        )
        payload = _judge_client_payloads(client)
        assert "Description: original task for [TAG_1]" in payload
        assert EMAIL_SEED not in payload

    async def test_raw_error_echoing_pii_is_pseudonymized_before_judge(self) -> None:
        """ErrorEvent.error is stored RAW at capture (T2 field list), so an
        error echoing user input reaches the stored payload verbatim and
        would leak into the judge transcript unless audit re-pseudonymizes
        it before judging."""
        session_id = uuid4()
        session_repo = InMemorySessionRepository()
        await _seed_conversation(session_repo, session_id, ["Do the thing."])
        events = _pii_echo_events(session_id)
        events[-1] = ErrorEvent(
            session_id,
            error=f"aborted: could not reach {EMAIL_SEED}",
            code="max_iterations",
        )
        trace_repo = await _capture_turn(events)
        # Capture stores error.error as-is — the seed sits raw in the store,
        # so only the audit-time scrub can protect the judge.
        assert EMAIL_SEED in trace_repo.rows[-1].payload["error"]

        client = _consistent_judge_client()
        verdict = await audit_session(
            session_id,
            session_repo=session_repo,
            trace_repo=trace_repo,
            judge=TrajectoryJudge(client),
            pseudonymizer=FakePseudonymizer(),
        )
        assert verdict.partial_credit is not None  # the judge actually ran
        payload = _judge_client_payloads(client)
        assert EMAIL_SEED not in payload  # raw error never reached the judge
        # The error line itself carries the placeholder — scrubbed, not dropped.
        assert "could not reach [TAG_1]" in payload


# ────────────────────────────────────────────────────────────────────────────
# Fail-closed audit
# ────────────────────────────────────────────────────────────────────────────


class TestFailClosed:
    """A broken pseudonymizer or unusable trace fails the audit closed:
    an explicit error and NO judge call."""

    async def test_pseudonymizer_down_fails_closed_no_judge_call(self) -> None:
        session_id = uuid4()
        session_repo = InMemorySessionRepository()
        await _seed_conversation(session_repo, session_id, [f"hi {EMAIL_SEED}"])
        trace_repo = await _capture_turn(_pii_echo_events(session_id))
        client = _consistent_judge_client()
        pseudonymizer = FailingPseudonymizer()

        with pytest.raises(TraceAuditError, match="failed closed"):
            await audit_session(
                session_id,
                session_repo=session_repo,
                trace_repo=trace_repo,
                judge=TrajectoryJudge(client),
                pseudonymizer=pseudonymizer,
            )
        assert pseudonymizer.calls == [f"hi {EMAIL_SEED}"]
        assert client.calls == []  # the judge was never called
        assert not client.exhausted  # no queued response was consumed

    async def test_untraced_session_fails_closed_no_judge_call(self) -> None:
        session_id = uuid4()
        session_repo = InMemorySessionRepository()
        await _seed_conversation(session_repo, session_id, ["Do the thing."])
        client = _consistent_judge_client()
        with pytest.raises(TraceAuditError, match="no captured loop events"):
            await audit_session(
                session_id,
                session_repo=session_repo,
                trace_repo=InMemoryTraceRepository(),
                judge=TrajectoryJudge(client),
                pseudonymizer=FakePseudonymizer(),
            )
        assert client.calls == []

    async def test_corrupt_stored_payload_fails_closed_no_judge_call(self) -> None:
        session_id = uuid4()
        session_repo = InMemorySessionRepository()
        await _seed_conversation(session_repo, session_id, ["Do the thing."])
        repo = InMemoryTraceRepository()
        await repo.insert_event(
            session_id,
            0,
            "telepathy",
            {"event_type": "telepathy", "session_id": str(session_id)},
        )
        client = _consistent_judge_client()
        with pytest.raises(TraceAuditError, match="unknown event_type"):
            await audit_session(
                session_id,
                session_repo=session_repo,
                trace_repo=repo,
                judge=TrajectoryJudge(client),
                pseudonymizer=FakePseudonymizer(),
            )
        assert client.calls == []

    async def test_session_without_user_message_fails_closed(self) -> None:
        session_id = uuid4()
        session_repo = InMemorySessionRepository()
        await session_repo.add_message(session_id, MessageRole.ASSISTANT, "hello?")
        trace_repo = await _capture_turn(_pii_echo_events(session_id))
        client = _consistent_judge_client()
        with pytest.raises(TraceAuditError, match="no user message"):
            await audit_session(
                session_id,
                session_repo=session_repo,
                trace_repo=trace_repo,
                judge=TrajectoryJudge(client),
                pseudonymizer=FakePseudonymizer(),
            )
        assert client.calls == []


# ────────────────────────────────────────────────────────────────────────────
# Default wiring (settings-based judge + pseudonymizer when not injected)
# ────────────────────────────────────────────────────────────────────────────


class TestDefaultWiring:
    """audit_session builds judge and pseudonymizer from settings when they
    are not injected."""

    async def test_defaults_are_constructed_and_used(self, monkeypatch) -> None:
        session_id = uuid4()
        session_repo = InMemorySessionRepository()
        await _seed_conversation(session_repo, session_id, ["Do the thing."])
        trace_repo = await _capture_turn(_pii_echo_events(session_id))
        client = _consistent_judge_client()
        default_judge = TrajectoryJudge(client)
        default_pseudonymizer = FakePseudonymizer()

        judge_settings: list[Settings] = []
        pseudonymizer_settings: list[Settings] = []
        settings = Settings(llm_api_key="test-key", _env_file=None)
        monkeypatch.setattr(
            "cortex.config.loader.get_settings", lambda: settings
        )
        monkeypatch.setattr(
            trace_audit, "_default_judge", lambda s: judge_settings.append(s) or default_judge
        )
        monkeypatch.setattr(
            trace_audit,
            "_default_pseudonymizer",
            lambda s: pseudonymizer_settings.append(s) or default_pseudonymizer,
        )

        verdict = await audit_session(
            session_id, session_repo=session_repo, trace_repo=trace_repo
        )
        assert verdict.partial_credit is not None
        assert judge_settings == [settings]
        assert pseudonymizer_settings == [settings]
        assert len(client.calls) == 2  # the default-built judge ran the passes

    def test_default_judge_constructor_from_settings(self) -> None:
        settings = Settings(llm_api_key="test-key", llm_provider="openai", _env_file=None)
        assert isinstance(trace_audit._default_judge(settings), TrajectoryJudge)

    def test_default_pseudonymizer_constructor_from_settings(self) -> None:
        from cortex.trace.pseudonymizer import RizzoPseudonymizer

        settings = Settings(
            llm_api_key="test-key",
            trace_sidecar_url="http://sidecar:5005",
            trace_sidecar_timeout_s=3.0,
            _env_file=None,
        )
        pseudonymizer = trace_audit._default_pseudonymizer(settings)
        assert isinstance(pseudonymizer, RizzoPseudonymizer)
        assert pseudonymizer._base_url == "http://sidecar:5005"


# ────────────────────────────────────────────────────────────────────────────
# Structural pin: context builder never depends on the trace repository
# ────────────────────────────────────────────────────────────────────────────


class TestContextBuilderIndependence:
    """Traces are never fed back into prompts/context: the context builder's
    data sources remain the session repository + context provider. These
    checks fail if a cortex.trace import appears in the module or anywhere in
    its import chain."""

    def test_context_builder_source_imports_no_trace(self) -> None:
        import cortex.agentic.context_builder as context_builder

        path = Path(context_builder.__file__)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        assert imports, "expected to find imports in the module"
        assert not any(
            name == "cortex.trace" or name.startswith("cortex.trace.")
            for name in imports
        ), "context builder must not import cortex.trace"
        # Positive pin: its data sources are still sessions + context provider.
        assert any(
            name.startswith("cortex.sessions.") for name in imports
        ), "context builder must keep reading the session repository"
        assert any(
            name.startswith("cortex.memory.context_provider") for name in imports
        ), "context builder must keep reading the context provider"

    def test_context_builder_runtime_import_chain_loads_no_trace(self) -> None:
        """Fresh-interpreter import: nothing under cortex.trace may be loaded
        (catches transitive dependencies the source scan cannot see)."""
        code = (
            "import sys\n"
            "import cortex.agentic.context_builder\n"
            "deps = sorted(m for m in sys.modules\n"
            "              if m == 'cortex.trace' or m.startswith('cortex.trace.'))\n"
            "print('TRACE_DEPS=' + ','.join(deps))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert "TRACE_DEPS=" in result.stdout
        deps = result.stdout.split("TRACE_DEPS=", 1)[1].strip()
        assert deps == "", f"context builder import chain loaded trace modules: {deps}"
