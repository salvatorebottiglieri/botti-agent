"""Eval tasks carry a Rule set and evaluate without ever learning (#175).

The spec's Evaluation row (``docs/specs/rule-system.md``): learning is off
during eval — the active rules are loaded as fixtures, an eval measures
behaviour with rules and never modifies them. Three groups of tests pin it:

- the field: YAML ``rules: [{condition, action}]`` loads in fixture order as
  definitive, confidence-1.0 rules; a task without the key is unchanged and a
  malformed entry is a ``ValueError``.
- the bundle: those rules reach the reasoner's system prompt as
  ``- <condition> → <action>`` with no ``[provisional]`` marker, and a task
  without rules still sends the prompt it sends today.
- the attestation: with the learning seam spied to fail on any call, a
  rules-carrying suite runs green and leaves every fixture rule untouched.

Every run goes through the real harness with the scripted LLM client — never a
real API.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from cortex.eval.fixtures import EvalSuite, EvalTask, GoalState, load_suite
from cortex.eval.runner import SuiteResult, run_suite
from cortex.rules import queue
from cortex.rules.models import Rule, RuleOrigin, RuleState
from cortex.rules.service import RuleService
from tests.eval.fakes import ScriptedLLMClient

RULE_CONDITION = "the user asks for a sum"
RULE_ACTION = "write the result to answer.txt"
SECOND_CONDITION = "the answer is a number"
SECOND_ACTION = "use digits, never words"

RULES_TASK = f"""
suite:
  name: rules-in-context
  version: 1.0.0
tasks:
  - name: write-answer
    turns: ["Write 40 + 2 to answer.txt"]
    rules:
      - condition: {RULE_CONDITION}
        action: {RULE_ACTION}
      - condition: {SECOND_CONDITION}
        action: {SECOND_ACTION}
    goal:
      files:
        - path: answer.txt
          equals: "42"
"""

RULES_AND_PLAIN_TASKS = """
suite:
  name: mixed
  version: 1.0.0
tasks:
  - name: with-rules
    turns: ["Write 40 + 2 to answer.txt"]
    rules:
      - condition: the user asks for a sum
        action: write the result to answer.txt
    goal:
      files:
        - path: answer.txt
          equals: "42"
  - name: without-rules
    turns: ["Say hello"]
    goal:
      answer: "Hello"
"""


def _load(tmp_path: Path, body: str) -> EvalSuite:
    path = tmp_path / "suite.yaml"
    path.write_text(body, encoding="utf-8")
    return load_suite(path)


def _script_sum_task(client: ScriptedLLMClient) -> None:
    """The golden two-step behavior for the write-answer task."""
    client.queue_tool_call("file_write", {"path": "answer.txt", "content": "42"})
    client.queue_text("Done")


def _prompts(calls: list[list[object]]) -> list[str]:
    """The message contents of one chat call, in order."""
    return [str(getattr(message, "content", "")) for message in calls]


class TestRuleField:
    """The task's Rule set: what the YAML says becomes a list of Rules."""

    def test_rules_load_in_fixture_order(self, tmp_path):
        """Fixture order is prompt order, so it must survive the load."""
        task = _load(tmp_path, RULES_TASK).tasks[0]

        assert [(rule.condition, rule.action) for rule in task.rules] == [
            (RULE_CONDITION, RULE_ACTION),
            (SECOND_CONDITION, SECOND_ACTION),
        ]

    def test_a_task_without_the_key_has_no_rules(self, tmp_path):
        """No ``rules`` key means an empty set, not None."""
        task = _load(
            tmp_path,
            """
suite:
  name: plain
  version: 1.0.0
tasks:
  - name: t
    turns: ["hello"]
    goal:
      answer: "hi"
""",
        ).tasks[0]

        assert task.rules == []

    def test_a_fixture_rule_is_definitive_and_certain(self, tmp_path):
        """A fixture is a given, not a hypothesis: definitive, 1.0, dictated."""
        rule = _load(tmp_path, RULES_TASK).tasks[0].rules[0]

        assert rule.state is RuleState.DEFINITIVE
        assert rule.confidence == 1.0
        assert rule.origin is RuleOrigin.DICTATED
        assert rule.rationale == ""
        assert rule.declares_itself() is False

    @pytest.mark.parametrize(
        "entry",
        [
            pytest.param('action: "do it"', id="missing-condition"),
            pytest.param('condition: "   "\naction: "do it"', id="blank-condition"),
            pytest.param('condition: "when"\n', id="missing-action"),
            pytest.param('condition: "when"\naction: ""', id="blank-action"),
            pytest.param('condition: 42\naction: "do it"', id="non-string-condition"),
            pytest.param('condition: "when"\naction: ["do it"]', id="non-string-action"),
        ],
    )
    def test_a_malformed_rule_entry_is_a_value_error(self, tmp_path, entry):
        """Missing, blank or non-string text is refused, like every field."""
        # A multi-line entry keeps the indentation of the first line.
        indented_entry = entry.replace("\n", "\n        ")
        body = f"""
suite:
  name: bad
  version: 1.0.0
tasks:
  - name: t
    turns: ["hello"]
    rules:
      - {indented_entry}
    goal:
      answer: "hi"
"""
        with pytest.raises(ValueError, match=r"rules\[0\]"):
            _load(tmp_path, body)

    def test_a_non_list_rules_key_is_a_value_error(self, tmp_path):
        """``rules`` is a list of entries or it is a malformed task."""
        body = """
suite:
  name: bad
  version: 1.0.0
tasks:
  - name: t
    turns: ["hello"]
    rules:
      condition: "when"
      action: "do it"
    goal:
      answer: "hi"
"""
        with pytest.raises(ValueError, match="rules must be a list"):
            _load(tmp_path, body)

    def test_rules_can_be_set_on_a_suite_built_in_code(self):
        """The field is part of the task type, not only of the YAML path."""
        task = EvalTask(
            name="t",
            turns=["hello"],
            goal=GoalState(answer="hi"),
            rules=[Rule(condition="when", action="do it", state=RuleState.DEFINITIVE)],
        )

        assert task.rules[0].action == "do it"


class TestRulesInContext:
    """The fixture rules are what the reasoner sees — and only where set."""

    async def test_a_task_with_rules_sees_them_in_its_prompt(self, tmp_path):
        """The rule's text reaches the system prompt in the render format."""
        suite = _load(tmp_path, RULES_TASK)
        client = ScriptedLLMClient()
        _script_sum_task(client)

        await run_suite(suite, client)

        contents = _prompts(client.calls[0])
        assert [
            line for text in contents for line in text.splitlines() if line.startswith("- ")
        ] == [
            f"- {RULE_CONDITION} → {RULE_ACTION}",
            f"- {SECOND_CONDITION} → {SECOND_ACTION}",
        ]

    async def test_a_fixture_rule_is_not_announced_as_provisional(self, tmp_path):
        """A fixture is certain: no ``[provisional]`` marker, no declare line."""
        suite = _load(tmp_path, RULES_TASK)
        client = ScriptedLLMClient()
        _script_sum_task(client)

        await run_suite(suite, client)

        prompt = "\n".join(_prompts(client.calls[0]))
        assert f"- {RULE_CONDITION} → {RULE_ACTION}" in prompt
        assert "[provisional]" not in prompt
        assert "Provisional rules are not confirmed yet" not in prompt

    async def test_a_task_without_rules_sends_no_rule_section(self, tmp_path):
        """Rules are per task: the second task must not inherit the first's."""
        suite = _load(tmp_path, RULES_AND_PLAIN_TASKS)
        client = ScriptedLLMClient()
        _script_sum_task(client)
        client.queue_text("Hello")

        await run_suite(suite, client)

        assert len(client.calls) == 3
        # The first task must have seen its rule, or "no rule section" is vacuous.
        assert "- the user asks for a sum → write the result to answer.txt" in "\n".join(
            _prompts(client.calls[0])
        )
        plain_prompt = "\n".join(_prompts(client.calls[2]))
        assert "Rules learned with the user" not in plain_prompt
        assert "→" not in plain_prompt


class TestEvalNeverLearns:
    """A measurement must not mutate what it measures (spec: Evaluation)."""

    @pytest.fixture
    def learning_forbidden(self, monkeypatch):
        """The learning seam: any call during the run fails the test."""
        calls: list[str] = []

        def _spy(name: str):
            async def _forbidden(*args, **kwargs):
                calls.append(name)
                raise AssertionError(f"the eval run called RuleService.{name}")

            return _forbidden

        async def _forbidden_enqueue(*args, **kwargs):
            calls.append("enqueue_turn")
            raise AssertionError("the eval run called enqueue_turn")

        for name in ("record_use", "create_dictated", "create_from_correction"):
            monkeypatch.setattr(RuleService, name, _spy(name))
        monkeypatch.setattr(queue, "enqueue_turn", _forbidden_enqueue)
        return calls

    async def test_a_rules_carrying_run_learns_nothing(
        self, tmp_path, learning_forbidden
    ):
        """Green run, live rules, no creation, no use, rules unchanged."""
        suite = _load(tmp_path, RULES_TASK)
        task = suite.tasks[0]
        before = [dataclasses.replace(rule) for rule in task.rules]
        ids_before = [rule.id for rule in task.rules]
        client = ScriptedLLMClient()
        _script_sum_task(client)

        result: SuiteResult = await run_suite(suite, client)

        assert result.results[0].passed is True
        # The fixture was live: without this, "learned nothing" is vacuous.
        assert f"- {RULE_CONDITION} → {RULE_ACTION}" in "\n".join(_prompts(client.calls[0]))
        assert learning_forbidden == []
        assert [rule.id for rule in task.rules] == ids_before
        assert task.rules == before
