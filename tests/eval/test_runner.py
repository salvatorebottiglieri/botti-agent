"""Integration tests for the suite runner: real AgentLoop + fake LLM.

These drive the real loop composition (Reasoner, ContextBuilder,
LoopExecutor, the four meta tools) with a scripted LLM client, so they
never hit a real API.
"""

import json
from collections.abc import AsyncGenerator
from uuid import UUID, uuid4

from cortex.agentic.events import LoopEvent, TextDeltaEvent
from cortex.eval.baseline import load_baseline
from cortex.eval.fixtures import EvalSuite, EvalTask, GoalFile, GoalState, SandboxFile
from cortex.eval.runner import _drive_turn, run_suite
from tests.eval.fakes import ScriptedLLMClient, scripted_judge_client


class TestRunSuite:
    """End-to-end suite runs against the real AgentLoop."""

    async def test_passing_task_writes_goal_file(self):
        """A task whose agent writes the goal file passes."""
        suite = EvalSuite(
            name="write",
            version="1.0.0",
            tasks=[
                EvalTask(
                    name="write-answer",
                    turns=["Write 42 to answer.txt"],
                    goal=GoalState(files=[GoalFile(path="answer.txt", equals="42")]),
                )
            ],
        )
        client = ScriptedLLMClient()
        client.queue_tool_call("file_write", {"path": "answer.txt", "content": "42"})
        client.queue_text("Done")

        result = await run_suite(suite, client)

        assert result.suite_name == "write"
        assert result.suite_version == "1.0.0"
        assert len(result.results) == 1
        task = result.results[0]
        assert task.passed is True
        assert task.message == "Done"
        assert task.failures == []
        assert task.metrics.iterations == 1
        assert task.metrics.tools_used == ["file_write"]
        assert task.metrics.tool_calls == 1

    async def test_failing_task_when_goal_not_reached(self):
        """A task whose agent responds without doing the work fails."""
        suite = EvalSuite(
            name="fail",
            version="1.0.0",
            tasks=[
                EvalTask(
                    name="write-answer",
                    turns=["Write 42 to answer.txt"],
                    goal=GoalState(files=[GoalFile(path="answer.txt", equals="42")]),
                )
            ],
        )
        client = ScriptedLLMClient()
        client.queue_text("I can't do that")

        result = await run_suite(suite, client, judge_client=scripted_judge_client(1))

        task = result.results[0]
        assert task.passed is False
        assert task.message == "I can't do that"
        assert task.metrics.iterations == 0
        assert task.metrics.tools_used == []
        assert any("answer.txt" in f for f in task.failures)

    async def test_sandbox_files_are_visible_to_tools(self):
        """Fixture sandbox files exist for the agent's tools."""
        suite = EvalSuite(
            name="read",
            version="1.0.0",
            tasks=[
                EvalTask(
                    name="copy-input",
                    turns=["Copy data/input.txt to output.txt"],
                    goal=GoalState(files=[GoalFile(path="output.txt", equals="40\n2")]),
                    sandbox=[SandboxFile(path="data/input.txt", content="40\n2")],
                )
            ],
        )
        client = ScriptedLLMClient()
        client.queue_tool_call(
            "file_write",
            {"path": "output.txt", "content": "40\n2"},
        )
        client.queue_text("Copied")

        result = await run_suite(suite, client)

        task = result.results[0]
        assert task.passed is True
        assert task.metrics.tools_used == ["file_write"]

    async def test_multi_turn_task_accumulates_metrics(self):
        """Scripted turns share one session; metrics span the whole transcript."""
        suite = EvalSuite(
            name="multi",
            version="1.0.0",
            tasks=[
                EvalTask(
                    name="read-then-write",
                    turns=[
                        "Read data/input.txt",
                        "Write the number you read to answer.txt",
                    ],
                    goal=GoalState(files=[GoalFile(path="answer.txt", equals="42")]),
                    sandbox=[SandboxFile(path="data/input.txt", content="42")],
                )
            ],
        )
        client = ScriptedLLMClient()
        # Turn 1: read the file
        client.queue_tool_call("file_read", {"path": "data/input.txt"})
        client.queue_text("I read 42")
        # Turn 2: write the answer
        client.queue_tool_call("file_write", {"path": "answer.txt", "content": "42"})
        client.queue_text("Written")

        result = await run_suite(suite, client)

        task = result.results[0]
        assert task.passed is True
        assert task.metrics.tools_used == ["file_read", "file_write"]
        assert task.metrics.tool_calls == 2
        assert task.metrics.iterations == 2

    async def test_shell_tool_runs_against_sandbox(self):
        """The sandboxed shell tool works through the real loop."""
        suite = EvalSuite(
            name="shell",
            version="1.0.0",
            tasks=[
                EvalTask(
                    name="touch-file",
                    turns=["Run: echo 42 > answer.txt"],
                    goal=GoalState(files=[GoalFile(path="answer.txt", contains="42")]),
                )
            ],
        )
        client = ScriptedLLMClient()
        client.queue_tool_call("shell", {"command": "echo 42 > answer.txt"})
        client.queue_text("Done")

        result = await run_suite(suite, client)

        task = result.results[0]
        assert task.passed is True
        assert task.metrics.tools_used == ["shell"]

    async def test_suite_metrics_and_baseline(self, tmp_path):
        """Suite-level metrics and the versioned baseline are recorded."""
        suite = EvalSuite(
            name="mix",
            version="1.0.0",
            tasks=[
                EvalTask(
                    name="passes",
                    turns=["Write 42 to answer.txt"],
                    goal=GoalState(files=[GoalFile(path="answer.txt", contains="42")]),
                ),
                EvalTask(
                    name="fails",
                    turns=["Write 42 to other.txt"],
                    goal=GoalState(files=[GoalFile(path="other.txt", contains="42")]),
                ),
            ],
        )
        client = ScriptedLLMClient()
        client.queue_tool_call("file_write", {"path": "answer.txt", "content": "42"})
        client.queue_text("done")
        client.queue_text("nope")

        baseline_path = tmp_path / "baselines" / "mix-v1.0.0.json"
        result = await run_suite(
            suite, client, baseline_path=baseline_path, judge_client=scripted_judge_client(1)
        )

        assert result.metrics.task_count == 2
        assert result.metrics.pass_count == 1
        assert result.metrics.pass_rate == 0.5
        assert result.metrics.tools_used == ["file_write"]

        assert baseline_path.exists()
        baseline = load_baseline(baseline_path)
        assert baseline is not None
        assert baseline.suite_name == "mix"
        assert baseline.suite_version == "1.0.0"
        assert baseline.task_count == 2
        assert baseline.pass_count == 1
        assert baseline.metrics.pass_rate == 0.5
        assert baseline.created_at
        assert baseline.schema_version == 1

        raw = json.loads(baseline_path.read_text(encoding="utf-8"))
        assert raw["suite_name"] == "mix"
        assert "created_at" in raw

    async def test_sample_suite_runs_end_to_end(self, sample_suite: EvalSuite):
        """The shipped sample YAML suite runs: one pass, one fail."""
        client = ScriptedLLMClient()
        # write-answer task: read is optional; script writes the answer directly
        client.queue_tool_call("file_write", {"path": "answer.txt", "content": "42"})
        client.queue_text("Written answer.txt")
        # leave-marker task: agent never creates the marker
        client.queue_text("I'm done")

        result = await run_suite(sample_suite, client, judge_client=scripted_judge_client(1))

        assert result.suite_name == "sample"
        assert result.metrics.task_count == 2
        assert result.metrics.pass_count == 1
        assert [r.name for r in result.results] == ["write-answer", "leave-marker"]
        assert result.results[0].passed is True
        assert result.results[1].passed is False

    async def test_loop_error_after_goal_met_reports_not_passed(self):
        """A loop failure after the goal state was reached fails the task.

        The goal file is written on turn two, then the loop raises
        MaxIterationsError; the task must not be reported passed and the
        error must surface in the message rather than a stale earlier-turn
        message.
        """
        suite = EvalSuite(
            name="error-after-goal",
            version="1.0.0",
            tasks=[
                EvalTask(
                    name="write-then-exceed",
                    turns=[
                        "Acknowledge the request",
                        "Write 42 to answer.txt",
                    ],
                    goal=GoalState(files=[GoalFile(path="answer.txt", equals="42")]),
                )
            ],
        )
        client = ScriptedLLMClient()
        # Turn 1 completes normally, leaving an earlier-turn message behind.
        client.queue_text("On it")
        # Turn 2 writes the goal file, then the loop hits the iteration cap.
        client.queue_tool_call("file_write", {"path": "answer.txt", "content": "42"})

        result = await run_suite(
            suite, client, max_iterations=1, judge_client=scripted_judge_client(1)
        )

        task = result.results[0]
        assert task.passed is False
        assert any(f.startswith("loop error:") for f in task.failures)
        assert "exceeded 1 iterations" in task.message
        assert task.message != "On it"


class _TwoDeltaLoop:
    """Minimal stand-in for AgentLoop streaming two text deltas per turn."""

    async def stream_chat(
        self, session_id: UUID, user_message: str
    ) -> AsyncGenerator[LoopEvent, None]:
        yield TextDeltaEvent(session_id, delta="Hello")
        yield TextDeltaEvent(session_id, delta=" world")


class TestDriveTurn:
    """Per-turn response text must accumulate deltas in order."""

    async def test_multiple_delta_events_accumulate_in_order(self):
        """Two TextDeltaEvents in one turn concatenate, not last-wins."""
        events: list[LoopEvent] = []
        text = await _drive_turn(_TwoDeltaLoop(), uuid4(), "Say hello", events)

        assert text == "Hello world"
        deltas = [e.delta for e in events if isinstance(e, TextDeltaEvent)]
        assert deltas == ["Hello", " world"]
