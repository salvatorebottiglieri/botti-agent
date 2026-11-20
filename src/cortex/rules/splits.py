"""Reading the correction that splits a rule (docs/specs/rule-system.md ## Split).

One ``chat()`` per scope correction, one tolerant parse: the two children plus,
positionally, the child that inherits each of the parent's uses.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from cortex.llm.base import LLMClient
from cortex.llm.config import GenerationConfig
from cortex.llm.models import ChatMessage, Role
from cortex.rules.feedback import strip_json_wrapping
from cortex.rules.models import Rule, RuleSpec, RuleUse

_SYSTEM_PROMPT = (
    "You cut a rule that turned out too broad into two rules.\n"
    "Answer with JSON of the shape\n"
    '{"children": [{"condition": "...", "action": "...", "rationale": "..."},\n'
    '              {"condition": "...", "action": "...", "rationale": "..."}],\n'
    ' "assignment": [0, 1, ...]}\n'
    "The two conditions must be disjoint and together cover the original rule.\n"
    "Answer one child index (0 or 1) per use, in the order the uses are listed; "
    "no markdown."
)


@dataclass(frozen=True)
class SplitProposal:
    """Two children and, positionally, the child each of the parent's uses goes to."""

    children: tuple[RuleSpec, RuleSpec]
    # uses[i] -> child index (0 or 1)
    assignment: tuple[int, ...]


def _parse_child(item: object) -> RuleSpec:
    if not isinstance(item, dict):
        raise ValueError("a split child must be an object")
    condition = str(item.get("condition") or "").strip()
    action = str(item.get("action") or "").strip()
    if not condition or not action:
        raise ValueError("a split child needs a condition and an action")
    return RuleSpec(
        condition=condition, action=action, rationale=str(item.get("rationale") or "")
    )


def _parse_split(content: str, use_count: int) -> SplitProposal:
    """Parse the proposer's JSON into a ``SplitProposal``.

    Raises ``ValueError`` for anything a split cannot be made of: the service
    has to be handed two complementary children and one child per use.
    """
    try:
        data = json.loads(strip_json_wrapping(content))
    except json.JSONDecodeError as e:
        raise ValueError(f"Malformed JSON from LLM: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("LLM JSON must be an object with 'children' and 'assignment'")
    raw_children = data.get("children")
    if not isinstance(raw_children, list) or len(raw_children) != 2:
        raise ValueError("a split needs exactly two children")
    first, second = (_parse_child(item) for item in raw_children)
    raw_assignment = data.get("assignment")
    if not isinstance(raw_assignment, list) or len(raw_assignment) != use_count:
        raise ValueError(f"the assignment must name one child for each of the {use_count} uses")
    for index in raw_assignment:
        if not isinstance(index, int) or index not in (0, 1):
            raise ValueError("an assignment is a list of 0/1 child indexes")
    return SplitProposal(children=(first, second), assignment=tuple(raw_assignment))


class SplitProposer:
    """Proposes how to cut a rule, from the correction that showed it too broad."""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm_client = llm_client
        # A reading, not a generation: temperature 0.
        self._generation = GenerationConfig(temperature=0.0)

    async def propose(self, rule: Rule, uses: Sequence[RuleUse]) -> SplitProposal:
        """Propose two children for ``rule`` and where each of its uses belongs."""
        # Indexes, never ids: the run maps the index back to the use it holds.
        listed = "\n".join(
            f"{index} | {use.situation} → {use.outcome}"
            for index, use in enumerate(uses)
        )
        messages = [
            ChatMessage(
                role=Role.SYSTEM,
                content=(
                    f"{_SYSTEM_PROMPT}\nRule:\n{rule.condition} → {rule.action}\n"
                    f"Uses:\n{listed}"
                ),
            )
        ]
        result = await self._llm_client.chat(messages, generation_config=self._generation)
        return _parse_split(result.message.content or "", len(uses))
