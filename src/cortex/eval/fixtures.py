"""Eval task fixtures: the YAML schema and its loader (ADR-0014).

Schema
------
A fixture file is a YAML document with a ``suite`` header and a non-empty
``tasks`` list::

    suite:
      name: sample          # required, non-empty
      version: 1.0.0        # required, non-empty (baselines are versioned per suite)
    tasks:
      - name: write-answer  # required
        description: optional human-readable context
        sandbox:            # optional files materialized before the run
          files:
            - path: data/input.txt
              content: "40\\n2"
        turns:              # required; scripted user turns, run in one session
          - "Read data/input.txt and write the sum to answer.txt"
        goal:               # required; the annotated goal state (ADR-0015) —
                            # the only pass/fail oracle. At least one check.
          files:            # optional expected sandbox files
            - path: answer.txt
              equals: "42"  # exact content match (optional)
              contains: "4" # substring match (optional, alternative to equals)
          absent:           # optional paths that must not exist
            - tmp/scratch.txt
          answer: "42"      # optional final-response match: one exact string,
                            # or a list of accepted strings matched
                            # case/whitespace-insensitively (normalized)

The goal state is graded deterministically against the sandbox directory
after the run (and, for ``answer``, against the final response text).

Suites may ship a sibling manifest (see :func:`load_manifest`) pinning the
prompt, model, grading, and rubric versions the suite's baselines assume, and may be
checked for golden-set balance with :func:`validate_suite_balance`. Refusal
suites (E6) encode expected behavior in the goal, not in a field:
must-refuse tasks list accepted refusal phrasings as ``answer`` variants;
must-comply tasks list the accepted answer; must-not-tool-use tasks
additionally declare ``absent`` paths. Refusal suites must be balanced —
both must-refuse and must-comply tasks — enforced by
:func:`assert_balanced_refusal_suite` via the task-name convention
(``refuse-*`` / ``comply-*``): the runner
(:func:`cortex.eval.runner.run_suite`) applies it automatically to any suite
whose name starts with ``refusal``, and tests exercise the validator directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class SandboxFile:
    """A file materialized into the task sandbox before the run."""

    path: str
    content: str = ""


@dataclass
class GoalFile:
    """An expected file in the sandbox after the run."""

    path: str
    equals: str | None = None
    contains: str | None = None


@dataclass
class GoalState:
    """Annotated goal state — the deterministic pass/fail oracle (ADR-0015).

    ``answer`` matches the final response text: either one exact string
    (matched verbatim) or a list of accepted strings matched
    case/whitespace-insensitively (graded by :mod:`cortex.eval.grader`).
    """

    files: list[GoalFile] = field(default_factory=list)
    absent: list[str] = field(default_factory=list)
    answer: str | list[str] | None = None


#: Name-prefix convention classifying a refusal suite task's expected
#: behavior: ``refuse-*`` tasks require refusal, ``comply-*`` require an answer.
REFUSE_TASK_PREFIX = "refuse-"
COMPLY_TASK_PREFIX = "comply-"


def assert_balanced_refusal_suite(suite: EvalSuite) -> None:
    """Validate a refusal suite is balanced: must-refuse AND must-comply tasks.

    Refusal suites regression-test refusal behavior; a one-sided set would
    let a policy game the metric by always refusing (or always complying).
    Expected behavior is classified by task name — ``refuse-*`` tasks require
    refusal, ``comply-*`` tasks require an answer — and every task must be
    one or the other.

    Raises:
        ValueError: If the suite lacks must-refuse tasks, lacks must-comply
            tasks, or contains a task with neither prefix.
    """
    refuse = [t for t in suite.tasks if t.name.startswith(REFUSE_TASK_PREFIX)]
    comply = [t for t in suite.tasks if t.name.startswith(COMPLY_TASK_PREFIX)]
    unknown = [
        t.name
        for t in suite.tasks
        if not t.name.startswith(REFUSE_TASK_PREFIX)
        and not t.name.startswith(COMPLY_TASK_PREFIX)
    ]
    if unknown:
        raise ValueError(
            f"refusal suite tasks must be named refuse-* or comply-*; found: {', '.join(unknown)}"
        )
    if not refuse:
        raise ValueError("refusal suite must include at least one must-refuse task (refuse-*)")
    if not comply:
        raise ValueError("refusal suite must include at least one must-comply task (comply-*)")


@dataclass
class EvalTask:
    """A self-contained evaluation case (see module docstring for the schema)."""

    name: str
    turns: list[str]
    goal: GoalState
    description: str = ""
    sandbox: list[SandboxFile] = field(default_factory=list)


@dataclass
class EvalSuite:
    """A versioned set of Eval Tasks measuring one capability."""

    name: str
    version: str
    tasks: list[EvalTask]


@dataclass
class EvalManifest:
    """Version pins for a suite: prompt, model, grading, and rubric versions.

    Recorded in a plain YAML file beside the fixture so baselines and
    nightly runs stay comparable over time (CONTEXT.md: Eval Baseline).
    """

    suite_name: str
    suite_version: str
    prompt_version: str
    model: str
    grading_version: str
    rubric_version: str


def load_suite(path: str | Path) -> EvalSuite:
    """Load an :class:`EvalSuite` from a YAML fixture file.

    Raises:
        OSError: If the file cannot be read.
        ValueError: If the document does not match the task schema.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return _parse_suite(raw, str(path))


def load_manifest(path: str | Path) -> EvalManifest:
    """Load an :class:`EvalManifest` from a plain YAML manifest file.

    Expected shape::

        suite:
          name: capability
          version: 1.0.0
        prompt_version: <sha256 of the reasoner system prompt>
        model: <settings llm_model>
        grading_version: <grader GRADING_SCHEMA_VERSION>
        rubric_version: <judge RUBRIC_VERSION>

    Raises:
        OSError: If the file cannot be read.
        ValueError: If the document does not match the manifest shape.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    source = str(path)
    if not isinstance(raw, dict) or not isinstance(raw.get("suite"), dict):
        raise ValueError(f"{source}: manifest must have a 'suite' mapping")

    suite = raw["suite"]
    name = suite.get("name")
    version = suite.get("version")
    if not isinstance(name, str) or not name:
        raise ValueError(f"{source}: manifest suite.name must be a non-empty string")
    if not isinstance(version, str) or not version:
        raise ValueError(f"{source}: manifest suite.version must be a non-empty string")

    prompt_version = raw.get("prompt_version")
    if not isinstance(prompt_version, str) or not prompt_version:
        raise ValueError(
            f"{source}: manifest prompt_version must be a non-empty string"
        )

    model = raw.get("model")
    if not isinstance(model, str) or not model:
        raise ValueError(f"{source}: manifest model must be a non-empty string")

    grading_version = raw.get("grading_version")
    if isinstance(grading_version, bool) or not isinstance(
        grading_version, (str, int)
    ):
        raise ValueError(
            f"{source}: manifest grading_version must be a string or int"
        )
    grading_version_str = str(grading_version)
    if not grading_version_str:
        raise ValueError(f"{source}: manifest grading_version must be non-empty")

    rubric_version = raw.get("rubric_version")
    if isinstance(rubric_version, bool) or not isinstance(rubric_version, (str, int)):
        raise ValueError(f"{source}: manifest rubric_version must be a string or int")
    rubric_version_str = str(rubric_version)
    if not rubric_version_str:
        raise ValueError(f"{source}: manifest rubric_version must be non-empty")

    return EvalManifest(
        suite_name=name,
        suite_version=version,
        prompt_version=prompt_version,
        model=model,
        grading_version=grading_version_str,
        rubric_version=rubric_version_str,
    )


def validate_suite_balance(suite: EvalSuite) -> list[str]:
    """Return balance violations for a golden set; an empty list means balanced.

    The golden set must contain both kinds of cases (one-sided-optimization
    guard, CONTEXT.md: Golden Set): positive cases whose goal declares
    something that must happen (``files`` or ``answer``) and negative cases
    whose goal declares something that must NOT happen (``absent``) — the
    not-answer / must-not-tool-use tasks.
    """
    violations: list[str] = []
    has_positive = any(t.goal.files or t.goal.answer is not None for t in suite.tasks)
    has_negative = any(t.goal.absent for t in suite.tasks)
    if not has_positive:
        violations.append(
            "suite has no positive cases (tasks whose goal requires files or an answer)"
        )
    if not has_negative:
        violations.append(
            "suite has no negative cases (tasks whose goal declares absent paths — "
            "the not-answer / must-not-tool-use behavior)"
        )
    return violations


def _parse_suite(raw: Any, source: str) -> EvalSuite:
    if not isinstance(raw, dict) or not isinstance(raw.get("suite"), dict):
        raise ValueError(f"{source}: fixture must have a 'suite' mapping")

    suite = raw["suite"]
    name = suite.get("name")
    version = suite.get("version")
    if not isinstance(name, str) or not name:
        raise ValueError(f"{source}: suite.name must be a non-empty string")
    if not isinstance(version, str) or not version:
        raise ValueError(f"{source}: suite.version must be a non-empty string")

    tasks = raw.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError(f"{source}: suite must define at least one task")

    return EvalSuite(
        name=name,
        version=version,
        tasks=[_parse_task(task, source, i) for i, task in enumerate(tasks)],
    )


def _parse_task(raw: Any, source: str, index: int) -> EvalTask:
    where = f"{source}: tasks[{index}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: task must be a mapping")

    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"{where}: task name must be a non-empty string")

    turns = raw.get("turns")
    if not isinstance(turns, list) or not turns or not all(isinstance(t, str) for t in turns):
        raise ValueError(f"{where}: task turns must be a non-empty list of strings")

    goal_raw = raw.get("goal")
    if not isinstance(goal_raw, dict):
        raise ValueError(f"{where}: task goal must be a mapping")
    goal = _parse_goal(goal_raw, where)

    description = raw.get("description", "")
    if not isinstance(description, str):
        raise ValueError(f"{where}: task description must be a string")

    sandbox_raw = raw.get("sandbox", {})
    if not isinstance(sandbox_raw, dict):
        raise ValueError(f"{where}: task sandbox must be a mapping")
    files_raw = sandbox_raw.get("files", [])
    if not isinstance(files_raw, list):
        raise ValueError(f"{where}: sandbox.files must be a list")
    sandbox = [_parse_sandbox_file(f, where) for f in files_raw]

    return EvalTask(
        name=name,
        turns=list(turns),
        goal=goal,
        description=description,
        sandbox=sandbox,
    )


def _parse_sandbox_file(raw: Any, where: str) -> SandboxFile:
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: sandbox file must be a mapping")
    path = raw.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError(f"{where}: sandbox file path must be a non-empty string")
    content = raw.get("content", "")
    if not isinstance(content, str):
        raise ValueError(f"{where}: sandbox file content must be a string")
    return SandboxFile(path=path, content=content)


def _parse_goal(raw: dict[str, Any], where: str) -> GoalState:
    files_raw = raw.get("files", [])
    if not isinstance(files_raw, list):
        raise ValueError(f"{where}: goal.files must be a list")

    files: list[GoalFile] = []
    for file_raw in files_raw:
        if not isinstance(file_raw, dict):
            raise ValueError(f"{where}: goal file must be a mapping")
        path = file_raw.get("path")
        if not isinstance(path, str) or not path:
            raise ValueError(f"{where}: goal file path must be a non-empty string")
        equals = file_raw.get("equals")
        contains = file_raw.get("contains")
        if equals is not None and not isinstance(equals, str):
            raise ValueError(f"{where}: goal file equals must be a string")
        if contains is not None and not isinstance(contains, str):
            raise ValueError(f"{where}: goal file contains must be a string")
        files.append(GoalFile(path=path, equals=equals, contains=contains))

    absent = raw.get("absent", [])
    if not isinstance(absent, list) or not all(isinstance(a, str) for a in absent):
        raise ValueError(f"{where}: goal.absent must be a list of strings")

    answer = raw.get("answer")
    if answer is not None:
        if isinstance(answer, str):
            if not answer:
                raise ValueError(f"{where}: goal.answer must be a non-empty string")
        elif isinstance(answer, list) and all(isinstance(a, str) for a in answer):
            if not answer or any(not a for a in answer):
                raise ValueError(f"{where}: goal.answer variants must be non-empty strings")
        else:
            raise ValueError(f"{where}: goal.answer must be a string or a list of strings")

    goal = GoalState(files=files, absent=list(absent), answer=answer)
    if not (files or absent or answer):
        raise ValueError(f"{where}: goal must declare at least one check")
    return goal
