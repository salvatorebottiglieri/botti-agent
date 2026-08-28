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
          answer: "42"      # optional exact final-response text match

The goal state is graded deterministically against the sandbox directory
after the run (and, for ``answer``, against the final response text).
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
    """Annotated goal state — the deterministic pass/fail oracle (ADR-0015)."""

    files: list[GoalFile] = field(default_factory=list)
    absent: list[str] = field(default_factory=list)
    answer: str | None = None


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


def load_suite(path: str | Path) -> EvalSuite:
    """Load an :class:`EvalSuite` from a YAML fixture file.

    Raises:
        OSError: If the file cannot be read.
        ValueError: If the document does not match the task schema.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return _parse_suite(raw, str(path))


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
    if (
        not isinstance(turns, list)
        or not turns
        or not all(isinstance(t, str) for t in turns)
    ):
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
    sandbox = [
        _parse_sandbox_file(f, where) for f in files_raw
    ]

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
    if answer is not None and not isinstance(answer, str):
        raise ValueError(f"{where}: goal.answer must be a string")

    goal = GoalState(files=files, absent=list(absent), answer=answer)
    if not (files or absent or answer):
        raise ValueError(f"{where}: goal must declare at least one check")
    return goal
