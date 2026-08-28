"""Eval harness core (ADR-0014).

Runs an evaluation suite locally with one command (a pytest entry point in
``tests/eval/``). The harness composes the real ``AgentLoop`` — reasoner,
context builder, executor, and the four meta tools — inside a per-task
sandbox directory, with no new seams inside existing modules.

Public seam:

* :func:`load_suite` — YAML task fixtures (scripted user turns + annotated
  goal state)
* :func:`run_suite` — runs every task through the real loop with an
  injected (or factory-created) LLM client, grades goal states, records
  metrics and an optional versioned baseline
* :func:`record_baseline` / :func:`load_baseline` — per-suite baseline JSON
* :func:`load_manifest` — per-suite version pins (prompt/model/grading)
* :func:`validate_suite_balance` — golden-set positive/negative balance guard
* :class:`TaskSandbox` — per-task sandbox the tools execute against
"""

from cortex.eval.baseline import EvalBaseline, load_baseline, record_baseline
from cortex.eval.fixtures import (
    EvalManifest,
    EvalSuite,
    EvalTask,
    GoalFile,
    GoalState,
    SandboxFile,
    load_manifest,
    load_suite,
    validate_suite_balance,
)
from cortex.eval.grader import GradingResult, grade_goal
from cortex.eval.metrics import SuiteMetrics, TaskMetrics, collect_metrics
from cortex.eval.runner import SuiteResult, TaskResult, run_suite
from cortex.eval.sandbox import SandboxedTool, SandboxEscapeError, TaskSandbox

__all__ = [
    "EvalBaseline",
    "EvalManifest",
    "EvalSuite",
    "EvalTask",
    "GradingResult",
    "GoalFile",
    "GoalState",
    "SandboxEscapeError",
    "SandboxFile",
    "SandboxedTool",
    "SuiteMetrics",
    "SuiteResult",
    "TaskMetrics",
    "TaskResult",
    "TaskSandbox",
    "collect_metrics",
    "grade_goal",
    "load_baseline",
    "load_manifest",
    "load_suite",
    "record_baseline",
    "run_suite",
    "validate_suite_balance",
]
