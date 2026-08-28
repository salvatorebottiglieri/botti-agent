"""Tests for the loop task suite (E2, T4): fixture, balance, manifest, e2e.

The loop suite is the heart of the eval system: 30 scripted loop tasks run
against the real AgentLoop in a per-task sandbox, each with an annotated
goal state (expected sandbox filesystem state). Pass/fail is the
deterministic goal-state comparison (ADR-0015) — no LLM judge. The golden
set is balanced: 22 positive tool-using tasks (file_read / file_write /
grep / shell) and 8 negative tasks whose correct behavior is to NOT use
tools — ask clarification via [QUESTION] or respond without writing the
artifact (goal.answer clarification variants + goal.absent).

All tests run through the real harness with the scripted LLM client —
never a real API.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import pytest_asyncio

from cortex.agentic.reasoner import Reasoner
from cortex.config.loader import load_yaml_config
from cortex.config.models import ModelPricing
from cortex.eval.fixtures import (
    EvalSuite,
    load_manifest,
    load_suite,
    validate_suite_balance,
)
from cortex.eval.grader import GRADING_SCHEMA_VERSION
from cortex.eval.judge import DEFAULT_DIMENSION_ORDER, RUBRIC_VERSION
from cortex.eval.metrics import compute_pass_k
from cortex.eval.runner import SuiteResult, run_suite
from cortex.llm.config import GenerationConfig
from cortex.llm.models import ChatMessage, ChatResult, UsageStats
from cortex.tools.interfaces import ToolDefinition
from cortex.tools.registry import InMemoryToolRegistry
from tests.eval.fakes import ScriptedLLMClient, scripted_judge_client

FIXTURES = Path(__file__).parent / "fixtures"
SUITE_PATH = FIXTURES / "loop_suite.yaml"
MANIFEST_PATH = FIXTURES / "loop_manifest.yaml"

POSITIVE_COUNT = 22
NEGATIVE_COUNT = 8
TASK_COUNT = POSITIVE_COUNT + NEGATIVE_COUNT

#: Per-chat-call usage scripted for every loop response; with the pricing
#: below each chat call costs 500*0.5/1e6 + 50*1.5/1e6 = $0.000325.
USAGE = UsageStats(prompt_tokens=500, completion_tokens=50, total_tokens=550)
PRICING = ModelPricing(input_per_mtok=0.5, output_per_mtok=1.5)
COST_PER_CALL = 0.000325

#: Golden (reference) behavior per task: the exact scripted steps that
#: reach the goal state through the real loop, in order. Each step is one
#: chat call: ("tool", name, arguments) executes a real tool call,
#: ("text", ...) ends the turn with a response, ("question", ...) ends the
#: turn via the [QUESTION] clarification path. Reference solutions are
#: verified end-to-end by TestLoopSuiteEndToEnd.
GOLDEN: dict[str, list[tuple[str, ...]]] = {
    # ── Positive: tool-using tasks (file_read / file_write / grep / shell) ──
    "read-sum-numbers": [
        ("tool", "file_read", {"path": "data/numbers.txt"}),
        ("tool", "file_write", {"path": "answer.txt", "content": "42"}),
        ("text", "Written 42"),
    ],
    "read-uppercase": [
        ("tool", "file_read", {"path": "greeting.txt"}),
        ("tool", "file_write", {"path": "upper.txt", "content": "HELLO CORTEX"}),
        ("text", "Done"),
    ],
    "read-reverse-lines": [
        ("tool", "file_read", {"path": "data/poem.txt"}),
        ("tool", "file_write", {"path": "reversed.txt", "content": "second line\nfirst line"}),
        ("text", "Done"),
    ],
    "read-extract-name": [
        ("tool", "file_read", {"path": "data/profile.txt"}),
        ("tool", "file_write", {"path": "name.txt", "content": "Alice"}),
        ("text", "Done"),
    ],
    "read-copy-notes": [
        ("tool", "file_read", {"path": "data/notes.txt"}),
        ("tool", "file_write", {"path": "notes/backup.txt", "content": "Buy milk\nCall mom"}),
        ("text", "Done"),
    ],
    "grep-todo-lines": [
        ("tool", "grep", {"pattern": "TODO", "path": "data/tasks.md"}),
        (
            "tool",
            "file_write",
            {"path": "todos.txt", "content": "- [ ] TODO: implement parser\n- [ ] TODO: add tests"},
        ),
        ("text", "Done"),
    ],
    "grep-error-count": [
        ("tool", "grep", {"pattern": "error", "path": "data/log.txt"}),
        ("tool", "file_write", {"path": "error-count.txt", "content": "3"}),
        ("text", "3 errors"),
    ],
    "grep-import-lines": [
        ("tool", "grep", {"pattern": "^import ", "path": "data/app.py", "case_sensitive": True}),
        ("tool", "file_write", {"path": "imports.txt", "content": "import os\nimport sys\nimport json"}),
        ("text", "Done"),
    ],
    "grep-invert-comments": [
        ("tool", "shell", {"command": "grep -v '^#' data/config.ini > settings.txt"}),
        ("text", "Done"),
    ],
    "shell-mkdir-write": [
        ("tool", "shell", {"command": "mkdir -p reports"}),
        ("tool", "file_write", {"path": "reports/q1.txt", "content": "Q1 done"}),
        ("text", "Done"),
    ],
    "shell-sort-names": [
        ("tool", "shell", {"command": "sort data/names.txt > sorted.txt"}),
        ("text", "Done"),
    ],
    "shell-wc-lines": [
        ("tool", "shell", {"command": "wc -l < data/input.txt > line-count.txt"}),
        ("text", "3 lines"),
    ],
    "shell-list-count": [
        ("tool", "shell", {"command": "ls data | wc -l > file-count.txt"}),
        ("text", "3 files"),
    ],
    "shell-append-line": [
        ("tool", "shell", {"command": "echo finished >> data/log.txt"}),
        ("text", "Done"),
    ],
    "write-notes-from-body": [
        ("tool", "file_read", {"path": "data/body.txt"}),
        ("tool", "file_write", {"path": "notes.md", "content": "# Notes\nFirst line\nSecond line"}),
        ("text", "Done"),
    ],
    "write-inventory-list": [
        ("tool", "file_read", {"path": "data/items.txt"}),
        ("tool", "file_write", {"path": "inventory.txt", "content": "- apple\n- banana"}),
        ("text", "Done"),
    ],
    "two-turn-read-then-write": [
        ("tool", "file_read", {"path": "data/input.txt"}),
        ("text", "I read 42"),
        ("tool", "file_write", {"path": "answer.txt", "content": "42"}),
        ("text", "Written"),
    ],
    "two-turn-grep-then-write": [
        ("tool", "grep", {"pattern": "warning", "path": "data/log.txt"}),
        ("text", "Found 2 warnings"),
        (
            "tool",
            "file_write",
            {"path": "warnings.txt", "content": "warning: disk low\nwarning: cpu high"},
        ),
        ("text", "Written"),
    ],
    "two-turn-mkdir-then-write": [
        ("tool", "shell", {"command": "mkdir -p reports"}),
        ("text", "Created"),
        ("tool", "file_write", {"path": "reports/status.txt", "content": "done"}),
        ("text", "Written"),
    ],
    "write-json-config": [
        ("tool", "file_write", {"path": "config.json", "content": '{"mode": "fast", "retries": 3}'}),
        ("text", "Done"),
    ],
    "read-csv-top-scorer": [
        ("tool", "file_read", {"path": "data/scores.csv"}),
        ("tool", "file_write", {"path": "top.txt", "content": "name,score\nalice,9"}),
        ("text", "Done"),
    ],
    "grep-error-lines": [
        ("tool", "grep", {"pattern": "ERROR", "path": "data/errors.txt", "case_sensitive": True}),
        ("tool", "file_write", {"path": "errors-found.txt", "content": "ERROR: boom\nERROR: kaboom"}),
        ("text", "Done"),
    ],
    # ── Negative: correct behavior is to NOT use tools ──
    "neg-ambiguous-which-file": [
        ("question", "Which file should I read?"),
    ],
    "neg-underspecified-output-path": [
        ("question", "Where should I save it?"),
    ],
    "neg-missing-input-file": [
        ("text", "data/report.csv doesn't exist"),
    ],
    "neg-unclear-transform": [
        ("question", "What should I change?"),
    ],
    "neg-ask-before-overwrite": [
        ("question", "What settings would you like?"),
    ],
    "neg-refuse-destructive-command": [
        ("text", "I won't run that command."),
    ],
    "neg-ambiguous-output-format": [
        ("question", "Which format would you like?"),
    ],
    "neg-vague-debug-request": [
        ("question", "What symptom are you seeing?"),
    ],
}


def _queue_golden(client: ScriptedLLMClient, steps: list[tuple[str, ...]], usage: UsageStats) -> None:
    """Queue the reference solution for one task onto the scripted client."""
    for step in steps:
        kind = step[0]
        if kind == "tool":
            client.queue_tool_call(step[1], step[2], usage=usage)
        elif kind == "text":
            client.queue_text(step[1], usage=usage)
        elif kind == "question":
            client.queue_text(f"[QUESTION]{step[1]}[/QUESTION]", usage=usage)
        else:
            raise AssertionError(f"unknown golden step kind: {kind!r}")


class _TurnTwoContextGuardedClient(ScriptedLLMClient):
    """Scripted client that only serves a two-turn task's turn-2 response
    when the turn-2 request transcript still contains the turn-1 tool
    result (F3 pin).

    If inter-turn history were dropped, the turn-2 request would lack the
    turn-1 tool result, the guard raises, and the task fails through the
    real grading path — a scripted response can't paper over lost context.
    """

    def __init__(self, turn2_user_turn: str, turn1_tool_result: str) -> None:
        super().__init__()
        self._turn2_user_turn = turn2_user_turn
        self._turn1_tool_result = turn1_tool_result

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolDefinition] | None = None,
        generation_config: GenerationConfig | None = None,
    ) -> ChatResult:
        # The turn-2 decision call is the one whose request ends with the
        # turn-2 user message; it must still carry the turn-1 tool result.
        if messages[-1].content == self._turn2_user_turn:
            transcript = "\n".join(m.content or "" for m in messages)
            if self._turn1_tool_result not in transcript:
                raise AssertionError(
                    "turn-2 request lost the turn-1 tool result "
                    f"{self._turn1_tool_result!r}: {transcript!r}"
                )
        return await super().chat(
            messages, tools=tools, generation_config=generation_config
        )


def _positives(suite: EvalSuite) -> list:
    return [t for t in suite.tasks if not t.name.startswith("neg-")]


def _negatives(suite: EvalSuite) -> list:
    return [t for t in suite.tasks if t.name.startswith("neg-")]


@pytest.fixture(scope="module")
def loop_suite() -> EvalSuite:
    return load_suite(SUITE_PATH)


@pytest_asyncio.fixture(scope="module")
async def loop_result(loop_suite: EvalSuite) -> SuiteResult:
    """One scripted golden run of the full 30-task loop suite."""
    client = ScriptedLLMClient()
    for task in loop_suite.tasks:
        _queue_golden(client, GOLDEN[task.name], USAGE)
    result = await run_suite(loop_suite, client, pricing=PRICING)
    assert client.exhausted, "golden scripts must consume every scripted response"
    return result


class TestLoopFixture:
    """The golden set is a 30-task, sandboxed, goal-state-annotated fixture."""

    def test_loads_30_tasks(self, loop_suite: EvalSuite) -> None:
        assert loop_suite.name == "loop"
        assert loop_suite.version == "1.0.0"
        assert len(loop_suite.tasks) == TASK_COUNT

    def test_task_names_are_unique(self, loop_suite: EvalSuite) -> None:
        assert len({task.name for task in loop_suite.tasks}) == len(loop_suite.tasks)

    def test_every_task_has_one_to_three_scripted_turns(self, loop_suite: EvalSuite) -> None:
        for task in loop_suite.tasks:
            assert 1 <= len(task.turns) <= 3, task.name
            assert all(isinstance(turn, str) and turn.strip() for turn in task.turns)

    def test_every_task_has_a_description(self, loop_suite: EvalSuite) -> None:
        for task in loop_suite.tasks:
            assert task.description.strip(), task.name

    def test_positive_tasks_require_a_tool_reached_goal_file(self, loop_suite: EvalSuite) -> None:
        """Positives grade on an expected sandbox file state — tool use is
        required to reach it (no positive is answerable without tools)."""
        for task in _positives(loop_suite):
            assert task.goal.files, f"{task.name}: positive must declare goal files"
            for expected in task.goal.files:
                assert expected.equals is not None or expected.contains is not None

    def test_negative_tasks_declare_answer_variants_and_absent_paths(
        self, loop_suite: EvalSuite
    ) -> None:
        """Negatives encode clarification/refusal via answer variants and
        must-not-happen artifacts via goal.absent."""
        for task in _negatives(loop_suite):
            assert isinstance(task.goal.answer, list) and task.goal.answer, task.name
            assert task.goal.absent, f"{task.name}: negative must declare absent paths"

    def test_negative_goal_files_only_assert_sandbox_preservation(
        self, loop_suite: EvalSuite
    ) -> None:
        """A negative's goal.files may only assert an existing sandbox input
        is preserved — never require a newly created artifact."""
        for task in _negatives(loop_suite):
            sandbox_paths = {file.path for file in task.sandbox}
            for expected in task.goal.files:
                assert expected.path in sandbox_paths, (
                    f"{task.name}: goal file {expected.path} is not a sandbox input"
                )


class TestLoopBalance:
    """The golden set is balanced: positive tool-using AND negative no-tool tasks."""

    def test_balance_validator_accepts_suite(self, loop_suite: EvalSuite) -> None:
        assert validate_suite_balance(loop_suite) == []

    def test_golden_set_mixes_positive_and_negative_cases(self, loop_suite: EvalSuite) -> None:
        positives = _positives(loop_suite)
        negatives = _negatives(loop_suite)
        assert len(positives) == POSITIVE_COUNT
        assert len(negatives) == NEGATIVE_COUNT
        assert len(positives) > len(negatives)  # tool-using is the dominant behavior

    def test_golden_scripts_match_task_classification(self, loop_suite: EvalSuite) -> None:
        """Reference solutions match the classification: positives use tools,
        negatives use none (ask clarification or respond without tools)."""
        for task in loop_suite.tasks:
            tools = {step[1] for step in GOLDEN[task.name] if step[0] == "tool"}
            if task.name.startswith("neg-"):
                assert tools == set(), f"{task.name}: negative must not use tools"
            else:
                assert tools, f"{task.name}: positive must use tools"
                assert tools <= {"file_read", "file_write", "grep", "shell"}

    def test_golden_set_exercises_all_four_meta_tools(self) -> None:
        """The four meta tools (file_read, file_write, grep, shell) all appear."""
        all_tools = {
            step[1]
            for spec in GOLDEN.values()
            for step in spec
            if step[0] == "tool"
        }
        assert all_tools == {"file_read", "file_write", "grep", "shell"}


class TestLoopManifest:
    """The manifest pins prompt, model, and grading versions."""

    def test_manifest_matches_fixture(self, loop_suite: EvalSuite) -> None:
        manifest = load_manifest(MANIFEST_PATH)
        assert manifest.suite_name == loop_suite.name == "loop"
        assert manifest.suite_version == loop_suite.version == "1.0.0"

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

    def test_manifest_pins_rubric_version(self) -> None:
        """ADR-0015: the judge rubric version is pinned in the manifest
        alongside prompt/model/grading versions and must equal RUBRIC_VERSION
        — a rubric change forces a manifest bump."""
        assert load_manifest(MANIFEST_PATH).rubric_version == RUBRIC_VERSION

    def test_manifest_pins_model(self) -> None:
        """The model pin matches the live config source (F2).

        Compares the manifest against the configured model from
        config.yaml (via the loader's standard file discovery) instead of
        a hardcoded literal, so changing the model in the live
        configuration fails this pin and forces a manifest bump.
        """
        raw_config = load_yaml_config()
        llm = raw_config.get("llm", {})
        configured_model = llm.get("model") if isinstance(llm, dict) else None
        assert isinstance(configured_model, str) and configured_model, (
            "config.yaml must declare llm.model (the manifest's model pin source)"
        )
        assert load_manifest(MANIFEST_PATH).model == configured_model


class TestLoopSuiteEndToEnd:
    """The full suite runs through the real AgentLoop with scripted goldens."""

    async def test_golden_behavior_passes_every_task(
        self, loop_suite: EvalSuite, loop_result: SuiteResult
    ) -> None:
        """Every reference solution reaches its annotated goal state: 100%."""
        assert loop_result.suite_name == "loop"
        assert loop_result.suite_version == "1.0.0"
        assert len(loop_result.results) == TASK_COUNT
        assert [r.name for r in loop_result.results] == [
            t.name for t in loop_suite.tasks
        ]
        assert all(r.passed for r in loop_result.results)
        assert loop_result.metrics.task_count == TASK_COUNT
        assert loop_result.metrics.pass_count == TASK_COUNT
        assert loop_result.metrics.pass_rate == 1.0

    async def test_per_task_metrics_iterations_tools_usage_latency_cost(
        self, loop_result: SuiteResult
    ) -> None:
        """Loop tasks carry the full metric bar: iterations, tools used,
        tokens (usage), latency, and cost (T1 seam)."""
        by_name = {r.name: r for r in loop_result.results}
        for name, steps in GOLDEN.items():
            task = by_name[name]
            tool_steps = [s for s in steps if s[0] == "tool"]
            expected_tools = list(dict.fromkeys(s[1] for s in tool_steps))
            assert task.metrics.tools_used == expected_tools, name
            assert task.metrics.tool_calls == len(tool_steps), name
            assert task.metrics.iterations == len(tool_steps), name
            # A golden never scripts a failing tool call: grading only
            # checks the final file state, so a failed intermediate
            # read/grep would otherwise pass unnoticed.
            assert task.metrics.failed_calls == 0, name
            # Tokens: usage accumulated from the loop's ResponseDoneEvent.
            assert task.metrics.usage is not None, name
            assert task.metrics.usage.prompt_tokens == USAGE.prompt_tokens * len(steps), name
            assert task.metrics.usage.completion_tokens == USAGE.completion_tokens * len(steps), name
            # Cost: usage x pricing, one chat call per scripted step.
            assert task.metrics.cost_usd == pytest.approx(COST_PER_CALL * len(steps)), name
            # Latency: the loop's wall time for the task's transcript.
            assert isinstance(task.metrics.latency_ms, float), name
            assert task.metrics.latency_ms >= 0, name
        assert loop_result.metrics.total_cost_usd == pytest.approx(
            COST_PER_CALL * sum(len(s) for s in GOLDEN.values())
        )

    async def test_two_turn_context_survives_between_turns(
        self, loop_suite: EvalSuite
    ) -> None:
        """F3 pin: the turn-2 response depends on turn-1 context.

        The main e2e run scripts turn-2 responses unconditionally, so it
        would pass even if inter-turn history were dropped. Here the
        scripted client refuses to serve the turn-2 response unless the
        turn-2 request still carries the turn-1 tool result — the golden
        passes only if multi-turn context survives in the loop.
        """
        task = next(
            t for t in loop_suite.tasks if t.name == "two-turn-read-then-write"
        )
        client = _TurnTwoContextGuardedClient(task.turns[1], "42")
        _queue_golden(client, GOLDEN[task.name], USAGE)
        result = await run_suite(
            EvalSuite(
                name=loop_suite.name, version=loop_suite.version, tasks=[task]
            ),
            client,
            pricing=PRICING,
        )
        assert client.exhausted, "golden scripts must consume every scripted response"
        assert result.results[0].passed, result.results[0].failures

    async def test_negative_tasks_use_no_tools(self, loop_result: SuiteResult) -> None:
        """Negative tasks are graded by their answer, with zero tool use."""
        by_name = {r.name: r for r in loop_result.results}
        for name, steps in GOLDEN.items():
            if not name.startswith("neg-"):
                continue
            task = by_name[name]
            assert task.metrics.tools_used == [], name
            assert task.metrics.tool_calls == 0, name
            assert task.metrics.iterations == 0, name

    async def test_tool_happy_agent_fails_negative_task(
        self, loop_suite: EvalSuite
    ) -> None:
        """F4 counter-direction: a tool-happy agent fails a negative task.

        The balance claim — a compliant agent passes, a tool-happy agent
        fails — is provable, not inferred: script a tool-happy trajectory
        for neg-ambiguous-which-file (pick a file and write the
        absent-declared summary.txt instead of asking which file) and the
        task must FAIL.
        """
        task = next(
            t for t in loop_suite.tasks if t.name == "neg-ambiguous-which-file"
        )
        client = ScriptedLLMClient()
        client.queue_tool_call("file_read", {"path": "data/a.txt"}, usage=USAGE)
        client.queue_tool_call(
            "file_write", {"path": "summary.txt", "content": "alpha"}, usage=USAGE
        )
        client.queue_text("Done", usage=USAGE)
        judge_client = scripted_judge_client(1)
        result = await run_suite(
            EvalSuite(
                name=loop_suite.name, version=loop_suite.version, tasks=[task]
            ),
            client,
            pricing=PRICING,
            judge_client=judge_client,
        )
        task_result = result.results[0]
        assert client.exhausted, "tool-happy trajectory must be fully consumed"
        assert not task_result.passed, task_result.failures
        assert any("summary.txt" in failure for failure in task_result.failures)
        # A3: the failed task is judged (scripted) — a verdict is attached.
        assert judge_client.exhausted
        assert task_result.judge_verdict is not None
        assert task_result.judge_verdict.consistent is True
        assert task_result.judge_verdict.rubric_version == RUBRIC_VERSION
        assert len(task_result.judge_verdict.scores) == 4

    async def test_failed_task_gets_judge_verdict_passed_task_none(
        self, loop_suite: EvalSuite
    ) -> None:
        """A3: the judge runs only on FAILED tasks, via the runner seam.

        A two-task run — one passing, one failing — produces a scripted
        judge verdict on the failing TaskResult and none on the passing
        one; the judge client is injected (never a real API) and serves
        exactly the two order-swap passes of the failing task.
        """
        failing = next(
            t for t in loop_suite.tasks if t.name == "neg-ambiguous-which-file"
        )
        passing = next(
            t for t in loop_suite.tasks if t.name == "read-sum-numbers"
        )
        client = ScriptedLLMClient()
        # Tool-happy trajectory: writes the absent-declared summary.txt
        # instead of asking which file → the task FAILS.
        client.queue_tool_call("file_read", {"path": "data/a.txt"}, usage=USAGE)
        client.queue_tool_call(
            "file_write", {"path": "summary.txt", "content": "alpha"}, usage=USAGE
        )
        client.queue_text("Done", usage=USAGE)
        _queue_golden(client, GOLDEN["read-sum-numbers"], USAGE)
        judge_client = scripted_judge_client(1)
        result = await run_suite(
            EvalSuite(
                name=loop_suite.name,
                version=loop_suite.version,
                tasks=[failing, passing],
            ),
            client,
            pricing=PRICING,
            judge_client=judge_client,
        )
        assert client.exhausted, "golden scripts must consume every scripted response"
        assert judge_client.exhausted, "judge client must serve exactly the failing pass"
        by_name = {r.name: r for r in result.results}
        failed = by_name["neg-ambiguous-which-file"]
        passed = by_name["read-sum-numbers"]
        assert not failed.passed
        assert passed.passed
        assert failed.judge_verdict is not None
        assert failed.judge_verdict.consistent is True
        assert failed.judge_verdict.rubric_version == RUBRIC_VERSION
        assert set(failed.judge_verdict.scores) == set(DEFAULT_DIMENSION_ORDER)
        assert passed.judge_verdict is None

    async def test_pass1_equals_pass_rate_on_single_run(
        self, loop_suite: EvalSuite, loop_result: SuiteResult
    ) -> None:
        """v1: pass^1 over a single run equals the suite pass rate (100%)."""
        by_task = {r.name: [r.passed] for r in loop_result.results}
        assert compute_pass_k(by_task, 1) == loop_result.metrics.pass_rate == 1.0
        # A failing hypothetical run drops pass^1 exactly like pass_rate.
        by_task["read-sum-numbers"] = [False]
        assert compute_pass_k(by_task, 1) == pytest.approx(1 - 1 / TASK_COUNT)
