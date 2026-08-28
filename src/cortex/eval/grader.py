"""Deterministic goal-state grading (ADR-0015).

The annotated goal state — sandbox filesystem state and, optionally, the
exact final answer — is the only pass/fail oracle. Grading compares the
sandbox directory (and final response text) against the goal with plain
executable checks: file existence, exact content, substring, absence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cortex.eval.fixtures import GoalState
from cortex.eval.sandbox import TaskSandbox


@dataclass
class GradingResult:
    """Outcome of grading one task's goal state."""

    passed: bool
    failures: list[str] = field(default_factory=list)


def grade_goal(
    goal: GoalState,
    sandbox: TaskSandbox,
    final_message: str = "",
) -> GradingResult:
    """Compare sandbox state (and final answer) against the goal state.

    Every declared check must pass; failures carry a human-readable reason.
    """
    failures: list[str] = []
    for expected in goal.files:
        path = sandbox.confine(expected.path)
        if not path.is_file():
            failures.append(f"missing file: {expected.path}")
            continue
        content = path.read_text(encoding="utf-8")
        if expected.equals is not None and content != expected.equals:
            failures.append(
                f"{expected.path}: content {content!r} != expected {expected.equals!r}"
            )
        elif expected.contains is not None and expected.contains not in content:
            failures.append(
                f"{expected.path}: content {content!r} lacks {expected.contains!r}"
            )
    for absent_path in goal.absent:
        if sandbox.confine(absent_path).exists():
            failures.append(f"unexpected file: {absent_path}")
    if goal.answer is not None and final_message != goal.answer:
        failures.append(f"final answer {final_message!r} != expected {goal.answer!r}")
    return GradingResult(passed=not failures, failures=failures)
