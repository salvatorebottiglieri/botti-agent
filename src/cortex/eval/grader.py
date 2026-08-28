"""Deterministic goal-state grading (ADR-0015).

The annotated goal state — sandbox filesystem state and, optionally, the
exact final answer — is the only pass/fail oracle. Grading compares the
sandbox directory (and final response text) against the goal with plain
executable checks: file existence, exact content, substring, absence.

``goal.answer`` accepts a single string (exact match) or a list of
accepted variants (any-of, matched case/whitespace-insensitively via
:func:`normalize_answer`). Refusal tasks (E6) grade refusal-vs-compliance
by listing accepted refusal phrasings as variants; the loop's final
response is classified deterministically, never by an LLM judge.
:data:`GRADING_SCHEMA_VERSION` pins the grading semantics so suites can
record which grader their baselines assume.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from cortex.eval.fixtures import GoalState
from cortex.eval.sandbox import TaskSandbox

#: Version of the grading semantics; bump on any behavioral change. Suites
#: pin this in their manifest (``grading_version``).
GRADING_SCHEMA_VERSION = 1


def normalize_answer(text: str) -> str:
    """Normalize an answer for comparison: strip, lowercase, collapse whitespace.

    Makes exact-match grading insensitive to capitalization, leading/trailing
    whitespace, and irregular spacing between words.
    """
    return re.sub(r"\s+", " ", text.strip().lower())


def _answer_matches(final_message: str, accepted: str | list[str]) -> bool:
    """True when the final response satisfies the goal's answer expectation.

    A single string is an exact match (historical behavior); a list is an
    any-of match after :func:`normalize_answer`.
    """
    if isinstance(accepted, str):
        return final_message == accepted
    normalized = normalize_answer(final_message)
    return any(normalized == normalize_answer(variant) for variant in accepted)


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
            failures.append(f"{expected.path}: content {content!r} != expected {expected.equals!r}")
        elif expected.contains is not None and expected.contains not in content:
            failures.append(f"{expected.path}: content {content!r} lacks {expected.contains!r}")
    for absent_path in goal.absent:
        if sandbox.confine(absent_path).exists():
            failures.append(f"unexpected file: {absent_path}")
    if goal.answer is not None and not _answer_matches(final_message, goal.answer):
        failures.append(
            f"final answer {final_message!r} does not match any accepted answer {goal.answer!r}"
        )
    return GradingResult(passed=not failures, failures=failures)
