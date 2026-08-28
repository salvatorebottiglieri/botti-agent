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
* :func:`assert_balanced_refusal_suite` — refusal suites must mix
  must-refuse (``refuse-*``) and must-comply (``comply-*``) tasks
* :class:`TrajectoryJudge` — partial credit + diagnosis for FAILED tasks
  (G-Eval rubric, order-swap consistency, never the pass/fail oracle);
  :func:`measure_judge_agreement` — judge-vs-human agreement on the audit set;
  :func:`judge_trust_gate` — trust gate over the audit set before judge
  output is included in reports
"""

from cortex.eval.baseline import EvalBaseline, load_baseline, record_baseline
from cortex.eval.fixtures import (
    EvalManifest,
    EvalSuite,
    EvalTask,
    GoalFile,
    GoalState,
    SandboxFile,
    assert_balanced_refusal_suite,
    load_manifest,
    load_suite,
    validate_suite_balance,
)
from cortex.eval.grader import GradingResult, grade_goal
from cortex.eval.judge import (
    DEFAULT_DIMENSION_ORDER,
    DEFAULT_RUBRIC,
    INCONSISTENT_DIAGNOSIS,
    JUDGE_TRUST_MIN_DIMENSION_AGREEMENT,
    JUDGE_TRUST_MIN_VERDICT_QUALITY,
    ORDER_SWAP_DIMENSION_TOLERANCE,
    RUBRIC_VERSION,
    HumanLabels,
    JudgeAgreement,
    JudgeAudit,
    JudgeDimension,
    JudgeParseError,
    JudgeVerdict,
    Rubric,
    RubricCriterion,
    TrajectoryJudge,
    TrustGateResult,
    build_judge_client,
    judge_trust_gate,
    load_judge_audit,
    measure_judge_agreement,
    partial_credit_band,
    render_transcript,
)
from cortex.eval.metrics import SuiteMetrics, TaskMetrics, collect_metrics, compute_pass_k
from cortex.eval.runner import SuiteResult, TaskResult, run_suite
from cortex.eval.sandbox import SandboxedTool, SandboxEscapeError, TaskSandbox

__all__ = [
    "DEFAULT_DIMENSION_ORDER",
    "DEFAULT_RUBRIC",
    "EvalBaseline",
    "EvalManifest",
    "EvalSuite",
    "EvalTask",
    "GradingResult",
    "GoalFile",
    "GoalState",
    "HumanLabels",
    "INCONSISTENT_DIAGNOSIS",
    "JUDGE_TRUST_MIN_DIMENSION_AGREEMENT",
    "JUDGE_TRUST_MIN_VERDICT_QUALITY",
    "JudgeAgreement",
    "JudgeAudit",
    "JudgeDimension",
    "JudgeParseError",
    "JudgeVerdict",
    "ORDER_SWAP_DIMENSION_TOLERANCE",
    "RUBRIC_VERSION",
    "Rubric",
    "RubricCriterion",
    "SandboxEscapeError",
    "SandboxFile",
    "SandboxedTool",
    "SuiteMetrics",
    "SuiteResult",
    "TaskMetrics",
    "TaskResult",
    "TaskSandbox",
    "TrajectoryJudge",
    "TrustGateResult",
    "assert_balanced_refusal_suite",
    "build_judge_client",
    "collect_metrics",
    "compute_pass_k",
    "grade_goal",
    "judge_trust_gate",
    "load_baseline",
    "load_judge_audit",
    "load_manifest",
    "load_suite",
    "measure_judge_agreement",
    "partial_credit_band",
    "record_baseline",
    "render_transcript",
    "run_suite",
    "validate_suite_balance",
]
