"""Tests for the capability smoke suite (E3): fixture, balance, manifest, e2e.

The suite is 20 GAIA-style questions with normalized exact-match grading,
balanced between positive answer cases and negative refuse/not-answer cases.
All tests run against the real harness with the scripted LLM client — never
a real API.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from cortex.agentic.reasoner import Reasoner
from cortex.config.models import ModelPricing
from cortex.eval.fixtures import (
    EvalSuite,
    EvalTask,
    GoalState,
    load_manifest,
    load_suite,
    validate_suite_balance,
)
from cortex.eval.grader import GRADING_SCHEMA_VERSION
from cortex.eval.runner import run_suite
from cortex.llm.models import UsageStats
from cortex.tools.registry import InMemoryToolRegistry
from tests.eval.fakes import ScriptedLLMClient, scripted_judge_client

FIXTURES = Path(__file__).parent / "fixtures"
SUITE_PATH = FIXTURES / "capability_suite.yaml"
MANIFEST_PATH = FIXTURES / "capability_suite.manifest.yaml"


@pytest.fixture(scope="module")
def capability_suite() -> EvalSuite:
    return load_suite(SUITE_PATH)


class TestCapabilityFixture:
    """The golden set is a 20-question, environment-free fixture."""

    def test_loads_20_questions(self, capability_suite: EvalSuite) -> None:
        assert capability_suite.name == "capability"
        assert capability_suite.version == "1.0.0"
        assert len(capability_suite.tasks) == 20

    def test_every_question_is_single_turn_with_answer_variants(
        self, capability_suite: EvalSuite
    ) -> None:
        for task in capability_suite.tasks:
            assert len(task.turns) == 1
            assert task.sandbox == []  # no environment
            assert isinstance(task.goal.answer, list)
            assert len(task.goal.answer) >= 1

    def test_golden_set_is_balanced(self, capability_suite: EvalSuite) -> None:
        """Positive and negative cases coexist (one-sided-optimization guard)."""
        negatives = [t for t in capability_suite.tasks if t.name.startswith("refuse-")]
        positives = [t for t in capability_suite.tasks if not t.name.startswith("refuse-")]
        assert len(negatives) == 5
        assert len(positives) == 15
        for task in negatives:
            assert task.goal.absent, "negative cases declare must-not-happen paths"
        for task in positives:
            assert task.goal.answer is not None

    def test_balance_validator_accepts_suite(self, capability_suite: EvalSuite) -> None:
        assert validate_suite_balance(capability_suite) == []


class TestBalanceValidation:
    """The balance guard flags one-sided golden sets."""

    def test_positive_only_suite_is_unbalanced(self) -> None:
        suite = EvalSuite(
            name="one-sided",
            version="1.0.0",
            tasks=[
                EvalTask(
                    name="answer-1",
                    turns=["What is 1 + 1?"],
                    goal=GoalState(answer=["2"]),
                )
            ],
        )
        violations = validate_suite_balance(suite)
        assert any("negative" in v for v in violations)

    def test_negative_only_suite_is_unbalanced(self) -> None:
        suite = EvalSuite(
            name="one-sided",
            version="1.0.0",
            tasks=[
                EvalTask(
                    name="refuse-x",
                    turns=["Ambiguous question?"],
                    goal=GoalState(absent=["answer.txt"]),
                )
            ],
        )
        violations = validate_suite_balance(suite)
        assert any("positive" in v for v in violations)


class TestCapabilityManifest:
    """The manifest pins prompt, model, and grading versions."""

    def test_manifest_matches_fixture(self, capability_suite: EvalSuite) -> None:
        manifest = load_manifest(MANIFEST_PATH)
        assert manifest.suite_name == capability_suite.name == "capability"
        assert manifest.suite_version == capability_suite.version == "1.0.0"

    def test_manifest_pins_reasoner_prompt_hash(self) -> None:
        """prompt_version is the hash of the prompt the reasoner actually uses."""
        reasoner = Reasoner(
            llm_client=ScriptedLLMClient(), tool_registry=InMemoryToolRegistry()
        )
        prompt = reasoner._default_system_prompt()
        manifest = load_manifest(MANIFEST_PATH)
        assert (
            manifest.prompt_version
            == hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        )

    def test_manifest_pins_grading_version(self) -> None:
        assert load_manifest(MANIFEST_PATH).grading_version == str(
            GRADING_SCHEMA_VERSION
        )

    def test_manifest_pins_model(self) -> None:
        assert load_manifest(MANIFEST_PATH).model == "deepseek-chat"


#: Scripted final responses, one per question, in suite task order. Three are
#: deliberately wrong: two wrong numeric answers and one definitive answer to
#: a question whose right behavior is to refuse.
SCRIPTED = {
    "arithmetic-17x23": "391",
    "train-distance": "150 miles",
    "seconds-in-3-hours": "10800",
    "tallest-person": "Alice",
    "romeo-and-juliet": "William Shakespeare",
    "capital-of-france": "Paris",
    "chemical-symbol-gold": "AU",  # case-insensitive variant of "Au"
    "sqrt-144": "13",  # WRONG
    "percent-25-of-80": "20",
    "ww2-end-year": "1945",
    "largest-ocean": "the Pacific Ocean",
    "next-in-sequence": "32",
    "letters-in-encyclopedia": "11",  # WRONG
    "algebra-x-plus-5": "7",
    "original-price": "$60",
    "refuse-subjective-best-player": "Lionel Messi",  # WRONG: must refuse
    "refuse-underspecified-weather": "Which location?",
    "refuse-private-breakfast": "I don't have access to that information",
    "refuse-future-winner": "I can't predict the future",
    "refuse-unsolvable-equation": "There is no solution",
}

WRONG = {"sqrt-144", "letters-in-encyclopedia", "refuse-subjective-best-player"}


class TestCapabilityEndToEnd:
    """The suite runs through the harness with per-question pass/fail."""

    async def test_scripted_run_reports_per_question_results(
        self, capability_suite: EvalSuite
    ) -> None:
        client = ScriptedLLMClient()
        usage = UsageStats(prompt_tokens=1000, completion_tokens=100)
        for task in capability_suite.tasks:
            client.queue_text(SCRIPTED[task.name], usage=usage)
        pricing = ModelPricing(input_per_mtok=0.5, output_per_mtok=1.5)

        # 3 WRONG questions fail; script the judge for every failed task.
        result = await run_suite(
            capability_suite, client, pricing=pricing, judge_client=scripted_judge_client(3)
        )

        assert result.suite_name == "capability"
        assert result.suite_version == "1.0.0"
        assert len(result.results) == 20
        expected = [t.name not in WRONG for t in capability_suite.tasks]
        assert [r.passed for r in result.results] == expected
        assert result.metrics.task_count == 20
        assert result.metrics.pass_count == 17
        assert result.metrics.pass_rate == pytest.approx(0.85)

    async def test_each_question_carries_cost_and_latency(
        self, capability_suite: EvalSuite
    ) -> None:
        client = ScriptedLLMClient()
        usage = UsageStats(prompt_tokens=1000, completion_tokens=100)
        for task in capability_suite.tasks:
            client.queue_text(SCRIPTED[task.name], usage=usage)
        pricing = ModelPricing(input_per_mtok=0.5, output_per_mtok=1.5)

        result = await run_suite(
            capability_suite, client, pricing=pricing, judge_client=scripted_judge_client(3)
        )

        # One chat per question, so per-question cost = 1000/1M * $0.5
        # + 100/1M * $1.5 = $0.00065; latency is the loop's wall time.
        for task in result.results:
            assert task.metrics.cost_usd == pytest.approx(0.00065)
            assert isinstance(task.metrics.latency_ms, float)
            assert task.metrics.latency_ms >= 0
