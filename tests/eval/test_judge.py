"""Tests for the Trajectory Judge (T5, ADR-0015).

The judge evaluates the problem-solving path of FAILED E2 tasks: G-Eval
rubric (criteria written before judging, per dimension), chain-of-thought,
form-fill Likert, averaged over samples; partial credit in [0,1] plus a
diagnosis per dimension; order-swap consistency (only consistent verdicts
accepted); ``llm_judge_model`` settings separation; and audit-set
judge-vs-human agreement. All LLM calls go through a scripted client — no
real API.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from cortex.agentic.events import (
    ErrorEvent,
    LoopEvent,
    ThinkingEvent,
    ToolResultEvent,
    ToolStartEvent,
)
from cortex.config.models import Settings
from cortex.eval.judge import (
    DEFAULT_DIMENSION_ORDER,
    DEFAULT_RUBRIC,
    INCONSISTENT_DIAGNOSIS,
    JUDGE_TRUST_MIN_DIMENSION_AGREEMENT,
    JUDGE_TRUST_MIN_VERDICT_QUALITY,
    ORDER_SWAP_DIMENSION_TOLERANCE,
    RUBRIC_VERSION,
    HumanLabels,
    JudgeAudit,
    JudgeDimension,
    JudgeParseError,
    JudgeVerdict,
    TrajectoryJudge,
    build_judge_client,
    judge_trust_gate,
    load_judge_audit,
    measure_judge_agreement,
    partial_credit_band,
    render_transcript,
)
from cortex.llm.models import ChatMessage, ChatResult, Role
from cortex.llm.providers.openai import OpenAIClient
from tests.eval.fakes import ScriptedLLMClient

AUDIT_PATH = Path(__file__).parent / "fixtures" / "judge_audit.yaml"


def _transcript() -> list[LoopEvent]:
    """A plausible failed-task transcript: right tool, wrong arguments, churn."""
    session_id = uuid4()
    return [
        ThinkingEvent(session_id=session_id, message="Let me look at the emails."),
        ToolStartEvent(session_id=session_id, tool_name="list_emails", tool_call_id="call_1"),
        ToolResultEvent(
            session_id=session_id,
            tool_name="list_emails",
            tool_call_id="call_1",
            success=True,
            output="[1] refund request from alice",
        ),
        ToolStartEvent(session_id=session_id, tool_name="refund_email", tool_call_id="call_2"),
        ToolResultEvent(
            session_id=session_id,
            tool_name="refund_email",
            tool_call_id="call_2",
            success=False,
            error="no such order: 1234",
        ),
        ToolStartEvent(session_id=session_id, tool_name="refund_email", tool_call_id="call_3"),
        ToolResultEvent(
            session_id=session_id,
            tool_name="refund_email",
            tool_call_id="call_3",
            success=False,
            error="no such order: 1234",
        ),
        ErrorEvent(session_id=session_id, error="max iterations", code="max_iterations"),
    ]


def _scripted_response(text: str) -> ChatResult:
    return ChatResult(message=ChatMessage(role=Role.ASSISTANT, content=text))


def _form_text(
    scores: dict[str, int],
    diagnoses: dict[str, str] | None = None,
) -> str:
    """Build a form-fill judge response for the given per-dimension scores."""
    diagnoses = diagnoses or {dim: f"diag {dim}" for dim in scores}
    lines = ["The agent progressed partway toward the goal."]
    for dim in DEFAULT_DIMENSION_ORDER:
        lines.append(f"{dim.value}: {scores[dim.value]}")
        lines.append(f"{dim.value}_diagnosis: {diagnoses.get(dim.value, '')}")
    return "\n".join(lines)


def _judge(client: ScriptedLLMClient) -> TrajectoryJudge:
    return TrajectoryJudge(client)


class TestRubric:
    """The rubric is a versioned object with criteria per dimension."""

    def test_rubric_version_constant(self) -> None:
        assert RUBRIC_VERSION == "1"
        assert DEFAULT_RUBRIC.version == RUBRIC_VERSION

    def test_rubric_has_four_dimensions_with_criteria(self) -> None:
        dims = {criterion.dimension for criterion in DEFAULT_RUBRIC.criteria}
        assert dims == {
            JudgeDimension.TOOL_SELECTION,
            JudgeDimension.ARGUMENTS,
            JudgeDimension.EFFICIENCY,
            JudgeDimension.POLICY,
        }
        for criterion in DEFAULT_RUBRIC.criteria:
            assert criterion.criteria.strip(), f"{criterion.dimension} needs criteria text"
        assert len(DEFAULT_RUBRIC.criteria) == len(DEFAULT_DIMENSION_ORDER)

    def test_rubric_is_frozen(self) -> None:
        with pytest.raises(Exception):
            DEFAULT_RUBRIC.version = "2"  # type: ignore[misc]


class TestRubricApplication:
    """G-Eval: criteria embedded in the prompt, CoT, form-fill Likert, averaged."""

    async def test_prompt_embeds_rubric_cot_and_form(self) -> None:
        client = ScriptedLLMClient([_scripted_response(_form_text({"tool_selection": 4, "arguments": 2, "efficiency": 3, "policy": 5}))])
        judge = _judge(client)
        await judge.judge(
            _transcript(),
            task_name="refund-email",
            task_description="Refund a customer email",
            goal_summary="The refund was issued",
            samples=1,
        )
        system = client.calls[0][0].content or ""
        user = client.calls[0][1].content or ""
        # Criteria written before judging, one per dimension, with indicators.
        for criterion in DEFAULT_RUBRIC.criteria:
            assert criterion.criteria in system
            assert criterion.dimension.value in system
        # Chain-of-thought instructed before the form is filled.
        assert "chain-of-thought" in system.lower() or "reason step-by-step" in system.lower()
        assert "Likert" in system
        # Task context + rendered transcript in the user message.
        assert "refund-email" in user
        assert "Refund a customer email" in user
        assert "The refund was issued" in user
        assert "[tool_start]" in user
        assert "refund_email" in user

    async def test_judge_parses_likert_form_and_diagnoses(self) -> None:
        client = ScriptedLLMClient([_scripted_response(_form_text({"tool_selection": 4, "arguments": 2, "efficiency": 3, "policy": 5}))])
        verdict = await _judge(client).judge(
            _transcript(),
            task_name="refund-email",
            task_description="Refund a customer email",
            goal_summary="Refund issued",
            samples=1,
        )
        assert verdict.scores == {
            JudgeDimension.TOOL_SELECTION: 4.0,
            JudgeDimension.ARGUMENTS: 2.0,
            JudgeDimension.EFFICIENCY: 3.0,
            JudgeDimension.POLICY: 5.0,
        }
        assert verdict.diagnoses[JudgeDimension.TOOL_SELECTION] == "diag tool_selection"
        assert verdict.consistent is None  # no order-swap ran (not applicable)
        assert verdict.samples == 1
        assert verdict.rubric_version == RUBRIC_VERSION

    async def test_judge_averages_over_samples(self) -> None:
        sample_a = _form_text({"tool_selection": 4, "arguments": 2, "efficiency": 3, "policy": 5})
        sample_b = _form_text({"tool_selection": 2, "arguments": 4, "efficiency": 5, "policy": 3})
        client = ScriptedLLMClient([_scripted_response(sample_a), _scripted_response(sample_b)])
        verdict = await _judge(client).judge(
            _transcript(),
            task_name="refund-email",
            task_description="Refund a customer email",
            goal_summary="Refund issued",
            samples=2,
        )
        assert verdict.scores[JudgeDimension.TOOL_SELECTION] == 3.0
        assert verdict.scores[JudgeDimension.ARGUMENTS] == 3.0
        assert verdict.scores[JudgeDimension.EFFICIENCY] == 4.0
        assert verdict.scores[JudgeDimension.POLICY] == 4.0
        assert verdict.samples == 2

    async def test_missing_dimension_score_raises(self) -> None:
        text = _form_text({"tool_selection": 4, "arguments": 2, "efficiency": 3, "policy": 5})
        text = text.replace("policy: 5\n", "")
        client = ScriptedLLMClient([_scripted_response(text)])
        with pytest.raises(JudgeParseError):
            await _judge(client).judge(
                _transcript(),
                task_name="refund-email",
                task_description="Refund a customer email",
                goal_summary="Refund issued",
                samples=1,
            )

    async def test_samples_must_be_positive(self) -> None:
        client = ScriptedLLMClient()
        with pytest.raises(ValueError):
            await _judge(client).judge(
                _transcript(),
                task_name="t",
                task_description="d",
                goal_summary="g",
                samples=0,
            )

    def test_render_transcript_is_deterministic(self) -> None:
        events = _transcript()
        assert render_transcript(events) == render_transcript(events)
        rendered = render_transcript(events)
        assert "[tool_start]" in rendered
        assert "refund_email" in rendered
        assert "max iterations" in rendered


class TestPartialCredit:
    """Dimension scores aggregate to a normalized partial credit in [0,1]."""

    async def _verdict(self, scores: dict[str, int]) -> JudgeVerdict:
        client = ScriptedLLMClient([_scripted_response(_form_text(scores))])
        return await _judge(client).judge(
            _transcript(),
            task_name="t",
            task_description="d",
            goal_summary="g",
            samples=1,
        )

    async def test_all_five_is_full_credit(self) -> None:
        verdict = await self._verdict({"tool_selection": 5, "arguments": 5, "efficiency": 5, "policy": 5})
        assert verdict.partial_credit == 1.0

    async def test_all_one_is_zero_credit(self) -> None:
        verdict = await self._verdict({"tool_selection": 1, "arguments": 1, "efficiency": 1, "policy": 1})
        assert verdict.partial_credit == 0.0

    async def test_mixed_scores_normalize_linearly(self) -> None:
        verdict = await self._verdict({"tool_selection": 4, "arguments": 2, "efficiency": 3, "policy": 5})
        # mean Likert = (4+2+3+5)/4 = 3.5 → (3.5-1)/4 = 0.625
        assert verdict.partial_credit == pytest.approx(0.625)

    async def test_partial_credit_stays_in_unit_interval(self) -> None:
        verdict = await self._verdict({"tool_selection": 5, "arguments": 4, "efficiency": 5, "policy": 3})
        assert 0.0 <= verdict.partial_credit <= 1.0

    async def test_diagnosis_joins_dimensions(self) -> None:
        verdict = await self._verdict({"tool_selection": 4, "arguments": 2, "efficiency": 3, "policy": 5})
        assert "tool_selection" in verdict.diagnosis
        assert "arguments" in verdict.diagnosis

    def test_partial_credit_bands(self) -> None:
        assert partial_credit_band(0.0) == 0
        assert partial_credit_band(0.24) == 0
        assert partial_credit_band(0.25) == 1
        assert partial_credit_band(0.49) == 1
        assert partial_credit_band(0.5) == 2
        assert partial_credit_band(0.75) == 3
        assert partial_credit_band(1.0) == 3


class TestOrderSwap:
    """Candidate verdicts judged in both orders; only consistent ones accepted."""

    async def test_consistent_bands_are_accepted(self) -> None:
        forward = _form_text({"tool_selection": 4, "arguments": 4, "efficiency": 4, "policy": 4})
        reverse = _form_text({"tool_selection": 4, "arguments": 4, "efficiency": 4, "policy": 4})
        client = ScriptedLLMClient([_scripted_response(forward), _scripted_response(reverse)])
        verdict = await _judge(client).judge_with_order_swap(
            _transcript(),
            task_name="refund-email",
            task_description="Refund a customer email",
            goal_summary="Refund issued",
            samples=1,
        )
        assert verdict.consistent is True
        assert verdict.partial_credit == pytest.approx(0.75)
        assert verdict.diagnosis != INCONSISTENT_DIAGNOSIS
        assert verdict.samples == 2
        assert verdict.rubric_version == RUBRIC_VERSION

    async def test_band_mismatch_is_escalated(self) -> None:
        forward = _form_text({"tool_selection": 5, "arguments": 5, "efficiency": 5, "policy": 5})
        reverse = _form_text({"tool_selection": 1, "arguments": 1, "efficiency": 1, "policy": 1})
        client = ScriptedLLMClient([_scripted_response(forward), _scripted_response(reverse)])
        verdict = await _judge(client).judge_with_order_swap(
            _transcript(),
            task_name="refund-email",
            task_description="Refund a customer email",
            goal_summary="Refund issued",
            samples=1,
        )
        assert verdict.consistent is False
        assert verdict.partial_credit is None
        assert verdict.diagnosis == INCONSISTENT_DIAGNOSIS
        assert verdict.scores == {}

    async def test_reverse_pass_presents_dimensions_in_reverse_order(self) -> None:
        forward = _form_text({"tool_selection": 4, "arguments": 4, "efficiency": 4, "policy": 4})
        reverse = _form_text({"tool_selection": 4, "arguments": 4, "efficiency": 4, "policy": 4})
        client = ScriptedLLMClient([_scripted_response(forward), _scripted_response(reverse)])
        await _judge(client).judge_with_order_swap(
            _transcript(),
            task_name="t",
            task_description="d",
            goal_summary="g",
            samples=1,
        )
        assert len(client.calls) == 2
        first_system = client.calls[0][0].content or ""
        second_system = client.calls[1][0].content or ""
        first_order = [first_system.find(f"{dim.value}: <") for dim in DEFAULT_DIMENSION_ORDER]
        second_order = [second_system.find(f"{dim.value}: <") for dim in DEFAULT_DIMENSION_ORDER]
        assert all(pos >= 0 for pos in first_order + second_order)
        # Forward pass lists dimensions in canonical order; second pass reversed.
        assert first_order == sorted(first_order)
        assert second_order == sorted(second_order, reverse=True)

    async def test_consistent_pass_averages_both_orders(self) -> None:
        forward = _form_text({"tool_selection": 4, "arguments": 4, "efficiency": 4, "policy": 4})
        reverse = _form_text({"tool_selection": 5, "arguments": 5, "efficiency": 5, "policy": 5})
        client = ScriptedLLMClient([_scripted_response(forward), _scripted_response(reverse)])
        verdict = await _judge(client).judge_with_order_swap(
            _transcript(),
            task_name="t",
            task_description="d",
            goal_summary="g",
            samples=1,
        )
        # Both land in band 3 (0.75 and 1.0); scores average to 4.5 → 0.875.
        assert verdict.consistent is True
        assert verdict.scores[JudgeDimension.TOOL_SELECTION] == 4.5
        assert verdict.partial_credit == pytest.approx(0.875)

    async def test_diametric_order_swap_is_inconsistent(self) -> None:
        """F3: consistency is per dimension, never the aggregate band.

        forward {tool:1, policy:5} and reverse {tool:5, policy:1} both
        normalize to 0.25 (band 1), so the old band check accepted them and
        averaged; per-dimension they are diametrically opposed and the
        verdict must be escalated to a human.
        """
        forward = _form_text({"tool_selection": 1, "arguments": 1, "efficiency": 1, "policy": 5})
        reverse = _form_text({"tool_selection": 5, "arguments": 1, "efficiency": 1, "policy": 1})
        client = ScriptedLLMClient([_scripted_response(forward), _scripted_response(reverse)])
        verdict = await _judge(client).judge_with_order_swap(
            _transcript(),
            task_name="t",
            task_description="d",
            goal_summary="g",
            samples=1,
        )
        assert verdict.consistent is False
        assert verdict.partial_credit is None
        assert verdict.diagnosis == INCONSISTENT_DIAGNOSIS
        assert verdict.scores == {}

    async def test_same_band_per_dimension_mismatch_is_inconsistent(self) -> None:
        """F3: the same aggregate band is not enough — any per-dimension
        gap beyond the tolerance escalates the verdict."""
        forward = _form_text({"tool_selection": 5, "arguments": 4, "efficiency": 4, "policy": 2})
        reverse = _form_text({"tool_selection": 3, "arguments": 4, "efficiency": 4, "policy": 4})
        client = ScriptedLLMClient([_scripted_response(forward), _scripted_response(reverse)])
        verdict = await _judge(client).judge_with_order_swap(
            _transcript(),
            task_name="t",
            task_description="d",
            goal_summary="g",
            samples=1,
        )
        # Both passes normalize to (3.75-1)/4 = 0.6875 → band 2, yet the
        # tool_selection pass differs by 2 Likert points: inconsistent.
        assert partial_credit_band(0.6875) == 2
        assert verdict.consistent is False
        assert verdict.partial_credit is None
        assert verdict.diagnosis == INCONSISTENT_DIAGNOSIS

    async def test_within_tolerance_dimension_diffs_are_consistent(self) -> None:
        """F3: per-dimension diffs at or under ORDER_SWAP_DIMENSION_TOLERANCE
        stay consistent and are averaged per dimension across orders."""
        assert ORDER_SWAP_DIMENSION_TOLERANCE == 1
        forward = _form_text({"tool_selection": 4, "arguments": 2, "efficiency": 3, "policy": 5})
        reverse = _form_text({"tool_selection": 4, "arguments": 3, "efficiency": 3, "policy": 4})
        client = ScriptedLLMClient([_scripted_response(forward), _scripted_response(reverse)])
        verdict = await _judge(client).judge_with_order_swap(
            _transcript(),
            task_name="t",
            task_description="d",
            goal_summary="g",
            samples=1,
        )
        assert verdict.consistent is True
        assert verdict.scores[JudgeDimension.TOOL_SELECTION] == 4.0
        assert verdict.scores[JudgeDimension.ARGUMENTS] == 2.5
        assert verdict.scores[JudgeDimension.EFFICIENCY] == 3.0
        assert verdict.scores[JudgeDimension.POLICY] == 4.5


class TestSettings:
    """llm_judge_model is a separate, configurable setting from llm_model."""

    def test_judge_model_defaults_to_deepseek_reasoner(self) -> None:
        settings = Settings(llm_api_key="test-key")
        assert settings.llm_judge_model == "deepseek-reasoner"
        assert settings.llm_judge_model != settings.llm_model

    def test_judge_model_is_configurable(self) -> None:
        settings = Settings(llm_api_key="test-key", llm_judge_model="custom-judge")
        assert settings.llm_judge_model == "custom-judge"
        assert settings.llm_model == "gpt-4o"  # generator untouched

    def test_build_judge_client_uses_judge_model(self) -> None:
        settings = Settings(llm_api_key="test-key", llm_model="deepseek-chat", llm_judge_model="deepseek-reasoner")
        client = build_judge_client(settings)
        assert isinstance(client, OpenAIClient)
        assert client._model == "deepseek-reasoner"  # type: ignore[attr-defined]
        assert client._model != settings.llm_model  # type: ignore[attr-defined]

    def test_build_judge_client_rejects_unsupported_provider(self) -> None:
        settings = Settings(llm_api_key="test-key", llm_provider="anthropic")
        with pytest.raises(ValueError):
            build_judge_client(settings)


class TestAuditAgreement:
    """Judge-vs-human agreement is measured per dimension on the audit set."""

    def test_audit_fixture_has_ten_to_fifteen_entries(self) -> None:
        audit = load_judge_audit(AUDIT_PATH)
        assert 10 <= len(audit.entries) <= 15
        for entry in audit.entries:
            assert entry.task.strip()
            assert entry.summary.strip()
            assert isinstance(entry.judge_verdict_quality, bool)
            for dim in DEFAULT_DIMENSION_ORDER:
                assert 1 <= entry.scores[dim] <= 5

    def test_measure_agreement_exact_and_tolerance(self) -> None:
        verdicts = [
            JudgeVerdict(
                scores={JudgeDimension.TOOL_SELECTION: 4.0, JudgeDimension.ARGUMENTS: 2.0, JudgeDimension.EFFICIENCY: 3.0, JudgeDimension.POLICY: 5.0},
                partial_credit=0.625,
                diagnoses={},
                diagnosis="ok",
                consistent=True,
                rubric_version=RUBRIC_VERSION,
                samples=1,
            ),
            JudgeVerdict(
                scores={JudgeDimension.TOOL_SELECTION: 2.0, JudgeDimension.ARGUMENTS: 4.0, JudgeDimension.EFFICIENCY: 1.0, JudgeDimension.POLICY: 3.0},
                partial_credit=0.375,
                diagnoses={},
                diagnosis="ok",
                consistent=True,
                rubric_version=RUBRIC_VERSION,
                samples=1,
            ),
        ]
        labels = [
            HumanLabels(
                task="a",
                summary="s",
                scores={JudgeDimension.TOOL_SELECTION: 4, JudgeDimension.ARGUMENTS: 2, JudgeDimension.EFFICIENCY: 3, JudgeDimension.POLICY: 5},
                judge_verdict_quality=True,
            ),
            HumanLabels(
                task="b",
                summary="s",
                scores={JudgeDimension.TOOL_SELECTION: 3, JudgeDimension.ARGUMENTS: 4, JudgeDimension.EFFICIENCY: 2, JudgeDimension.POLICY: 3},
                judge_verdict_quality=True,
            ),
        ]
        # tolerance 0: tool_selection 4/4 ✓, 2 vs 3 ✗ → 0.5; arguments 2/2 ✓;
        # efficiency 3/3 ✓, 1 vs 2 ✗ → 0.5; policy 5/5 ✓, 3/3 ✓ → 1.0
        exact = measure_judge_agreement(verdicts, labels, tolerance=0)
        assert exact.per_dimension[JudgeDimension.TOOL_SELECTION] == 0.5
        assert exact.per_dimension[JudgeDimension.ARGUMENTS] == 1.0
        assert exact.per_dimension[JudgeDimension.EFFICIENCY] == 0.5
        assert exact.per_dimension[JudgeDimension.POLICY] == 1.0
        assert exact.overall == pytest.approx(0.75)
        # tolerance 1: everything within 1 → 1.0
        tolerant = measure_judge_agreement(verdicts, labels, tolerance=1)
        assert tolerant.overall == 1.0

    def test_measure_agreement_requires_equal_length(self) -> None:
        with pytest.raises(ValueError):
            measure_judge_agreement([], [HumanLabels(task="a", summary="s", scores={dim: 3 for dim in DEFAULT_DIMENSION_ORDER}, judge_verdict_quality=True)])

    def test_measure_agreement_rejects_negative_tolerance(self) -> None:
        with pytest.raises(ValueError):
            measure_judge_agreement([], [], tolerance=-1)

    def test_measure_agreement_requires_all_dimensions(self) -> None:
        verdict = JudgeVerdict(
            scores={JudgeDimension.TOOL_SELECTION: 4.0},
            partial_credit=None,
            diagnoses={},
            diagnosis="partial",
            consistent=False,
            rubric_version=RUBRIC_VERSION,
            samples=1,
        )
        label = HumanLabels(task="a", summary="s", scores={dim: 3 for dim in DEFAULT_DIMENSION_ORDER}, judge_verdict_quality=True)
        with pytest.raises(ValueError):
            measure_judge_agreement([verdict], [label])

    def test_load_judge_audit_rejects_rubric_version_mismatch(self, tmp_path) -> None:
        """A2: an audit whose rubric_version differs from RUBRIC_VERSION fails load."""
        audit = load_judge_audit(AUDIT_PATH)
        assert audit.rubric_version == RUBRIC_VERSION
        path = tmp_path / "audit.yaml"
        path.write_text("rubric_version: '2'\nentries: []\n", encoding="utf-8")
        with pytest.raises(ValueError, match="rubric_version"):
            load_judge_audit(path)


def _audit_entry(
    task: str, scores: dict[JudgeDimension, int], quality: bool = True
) -> HumanLabels:
    return HumanLabels(
        task=task, summary="s", scores=scores, judge_verdict_quality=quality
    )


def _verdict(scores: dict[JudgeDimension, float]) -> JudgeVerdict:
    return JudgeVerdict(
        scores=scores,
        partial_credit=0.5,
        diagnoses={},
        diagnosis="ok",
        consistent=True,
        rubric_version=RUBRIC_VERSION,
        samples=1,
    )


def _scores(score: int) -> dict[JudgeDimension, int]:
    return {dim: score for dim in DEFAULT_DIMENSION_ORDER}


class TestTrustGate:
    """The judge is trusted only on measured agreement with the audit set."""

    def test_gate_passes_with_high_agreement(self) -> None:
        labels = [_audit_entry(f"t{i}", _scores(3)) for i in range(12)]
        verdicts = [_verdict({dim: 3.0 for dim in DEFAULT_DIMENSION_ORDER}) for _ in labels]
        audit = JudgeAudit(rubric_version=RUBRIC_VERSION, entries=tuple(labels))
        result = judge_trust_gate(verdicts, audit)
        assert result.trusted is True
        assert result.reasons == ()
        assert result.agreement.overall == 1.0
        assert result.verdict_quality_rate == 1.0

    def test_gate_fails_below_dimension_threshold(self) -> None:
        """Per-dimension agreement below JUDGE_TRUST_MIN_DIMENSION_AGREEMENT fails."""
        labels = [_audit_entry(f"t{i}", _scores(3)) for i in range(12)]
        # tool_selection scores disagree with every human label (diff 3):
        verdicts = [
            _verdict(
                {
                    JudgeDimension.TOOL_SELECTION: 3.0 + 3.0,
                    JudgeDimension.ARGUMENTS: 3.0,
                    JudgeDimension.EFFICIENCY: 3.0,
                    JudgeDimension.POLICY: 3.0,
                }
            )
            for _ in labels
        ]
        audit = JudgeAudit(rubric_version=RUBRIC_VERSION, entries=tuple(labels))
        result = judge_trust_gate(verdicts, audit)
        assert result.trusted is False
        assert result.agreement.per_dimension[JudgeDimension.TOOL_SELECTION] == 0.0
        assert any("tool_selection" in reason for reason in result.reasons)
        assert any(
            f"{JUDGE_TRUST_MIN_DIMENSION_AGREEMENT}" in reason for reason in result.reasons
        )

    def test_gate_fails_closed_on_missing_audit(self) -> None:
        """An empty audit set fails closed: no calibration, no trust."""
        audit = JudgeAudit(rubric_version=RUBRIC_VERSION, entries=())
        result = judge_trust_gate([], audit)
        assert result.trusted is False
        assert any("empty" in reason for reason in result.reasons)
        assert result.verdict_quality_rate == 0.0

    def test_gate_consumes_judge_verdict_quality(self) -> None:
        """A4: exact-match verdict quality feeds the gate (dead data consumed)."""
        labels = [
            _audit_entry(f"t{i}", _scores(3), quality=False) for i in range(12)
        ]
        verdicts = [_verdict({dim: 3.0 for dim in DEFAULT_DIMENSION_ORDER}) for _ in labels]
        audit = JudgeAudit(rubric_version=RUBRIC_VERSION, entries=tuple(labels))
        result = judge_trust_gate(verdicts, audit)
        # Dimension agreement is perfect, but the human rejected every
        # verdict's quality: below JUDGE_TRUST_MIN_VERDICT_QUALITY → fail.
        assert result.agreement.overall == 1.0
        assert result.verdict_quality_rate == 0.0
        assert result.trusted is False
        assert any("verdict quality" in reason for reason in result.reasons)
        assert any(
            f"{JUDGE_TRUST_MIN_VERDICT_QUALITY}" in reason for reason in result.reasons
        )

    def test_gate_on_real_audit_fixture(self) -> None:
        """The shipped audit fixture drives the gate end-to-end.

        Verdicts matching every human label exactly clear the dimension bar
        (1.0 >= 0.8) but two fixture entries carry judge_verdict_quality:
        fail, so the exact-match verdict-quality bar (10/12 < 1.0) fails.
        """
        audit = load_judge_audit(AUDIT_PATH)
        verdicts = [
            _verdict({dim: float(entry.scores[dim]) for dim in DEFAULT_DIMENSION_ORDER})
            for entry in audit.entries
        ]
        result = judge_trust_gate(verdicts, audit)
        assert result.agreement.overall == 1.0
        assert result.verdict_quality_rate == pytest.approx(0.8333)
        assert result.trusted is False
        assert any("verdict quality" in reason for reason in result.reasons)
