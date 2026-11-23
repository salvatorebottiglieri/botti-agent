"""Attributing a turn's rule uses and proposals (docs/specs/rule-system.md slice B).

One ``chat()`` per turn, one tolerant parse. The caller sees only ``Feedback``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import UUID

from cortex.llm.base import LLMClient
from cortex.llm.config import GenerationConfig
from cortex.llm.models import ChatMessage, Role
from cortex.rules.models import Rule, RuleOrigin, RuleSpec, UseOutcome

_SYSTEM_PROMPT = (
    "You attribute the use of learned rules on one assistant turn.\n"
    "The rules that were in the assistant's context are listed one per line as\n"
    "<rule_id> | <condition> → <action>.\n"
    "For each rule, say whether the assistant followed it on this turn: use an outcome of\n"
    "approval, implicit, correction, scope_correction or neutral. Omit a rule that was\n"
    "neither applied nor contradicted; never invent a rule_id.\n"
    "If the user's reply teaches a new rule, propose it with condition, action and rationale.\n"
    "Answer with JSON of the shape\n"
    '{"uses": [{"rule_id": "...", "outcome": "..."}],\n'
    ' "proposals": [{"condition": "...", "action": "...", "rationale": "...", '
    '"origin": "dictated" | "correction"}]}\n'
    'Return {"uses": [], "proposals": []} when nothing applies; no markdown.'
)

# A proposal is born from the user's dictation or their correction of the
# assistant. A merge is produced by the rule service, never by a classifier.
_PROPOSAL_ORIGINS = (RuleOrigin.DICTATED, RuleOrigin.CORRECTION)


@dataclass(frozen=True)
class AttributedUse:
    """One rule the assistant applied on a turn, with the outcome it got."""

    rule_id: UUID
    outcome: UseOutcome


@dataclass(frozen=True)
class RuleProposal:
    """A rule the user's reply implied but did not dictate as one."""

    spec: RuleSpec
    origin: RuleOrigin


@dataclass(frozen=True)
class Feedback:
    """What one turn said about the rules in its context."""

    uses: list[AttributedUse] = field(default_factory=list)
    proposals: list[RuleProposal] = field(default_factory=list)


def strip_json_wrapping(content: str) -> str:
    """Strip the ``<think>`` block and markdown fences an LLM reply may wrap its JSON in."""
    # ``chat()`` prefixes out-of-band reasoning with an inline <think> block,
    # the same convention ``chat_stream()`` already emits; the JSON follows it.
    text = content.strip()
    text = re.sub(
        r"^<think>.*?</think>", "", text, count=1, flags=re.IGNORECASE | re.DOTALL
    ).strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_use(item: object, known_rule_ids: set[UUID]) -> AttributedUse | None:
    if not isinstance(item, dict):
        return None
    try:
        rule_id = UUID(str(item.get("rule_id")))
    except ValueError:
        return None
    if rule_id not in known_rule_ids:
        return None
    try:
        outcome = UseOutcome(str(item.get("outcome")))
    except ValueError:
        return None
    return AttributedUse(rule_id=rule_id, outcome=outcome)


def _parse_spec(item: object) -> RuleSpec | None:
    """The condition, action and rationale of a candidate rule; None when it is
    not an object or either field is blank."""
    if not isinstance(item, dict):
        return None
    condition = str(item.get("condition") or "").strip()
    action = str(item.get("action") or "").strip()
    if not condition or not action:
        return None
    return RuleSpec(
        condition=condition, action=action, rationale=str(item.get("rationale") or "")
    )


def _parse_proposal(item: object) -> RuleProposal | None:
    if not isinstance(item, dict):
        return None
    spec = _parse_spec(item)
    if spec is None:
        return None
    try:
        origin = RuleOrigin(str(item.get("origin")))
    except ValueError:
        origin = RuleOrigin.CORRECTION
    if origin not in _PROPOSAL_ORIGINS:
        origin = RuleOrigin.CORRECTION
    return RuleProposal(spec=spec, origin=origin)


def _parse_feedback(content: str, known_rule_ids: set[UUID]) -> Feedback:
    """Parse the classifier's JSON into a ``Feedback``, dropping what it cannot trust.

    Raises ``ValueError`` on malformed JSON or a missing ``uses`` key.
    """
    try:
        data = json.loads(strip_json_wrapping(content))
    except json.JSONDecodeError as e:
        raise ValueError(f"Malformed JSON from LLM: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("uses"), list):
        raise ValueError("LLM JSON must be an object with a 'uses' list")

    uses: list[AttributedUse] = []
    seen: set[UUID] = set()
    for item in data["uses"]:
        use = _parse_use(item, known_rule_ids)
        if use is None or use.rule_id in seen:
            continue
        seen.add(use.rule_id)
        uses.append(use)

    raw_proposals = data.get("proposals")
    proposals = [
        proposal
        for proposal in (
            _parse_proposal(item)
            for item in (raw_proposals if isinstance(raw_proposals, list) else [])
        )
        if proposal is not None
    ]
    # One turn yields at most one new rule; a second proposal is ignored.
    return Feedback(uses=uses, proposals=proposals[:1])


class FeedbackClassifier:
    """Turns one assistant turn plus the user's reply into a ``Feedback``."""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm_client = llm_client
        # Attribution is a classification: temperature 0.
        self._generation = GenerationConfig(temperature=0.0)

    async def classify(
        self, rules: Sequence[Rule], assistant_turn: str, user_message: str
    ) -> Feedback:
        """Classify one turn. Silence, or no rule in context, buys no provider call."""
        if not user_message.strip() or not rules:
            return Feedback()
        listed = "\n".join(f"{r.id} | {r.condition} → {r.action}" for r in rules)
        messages = [
            ChatMessage(role=Role.SYSTEM, content=f"{_SYSTEM_PROMPT}\nRules in context:\n{listed}"),
            ChatMessage(role=Role.ASSISTANT, content=assistant_turn),
            ChatMessage(role=Role.USER, content=user_message),
        ]
        result = await self._llm_client.chat(messages, generation_config=self._generation)
        return _parse_feedback(
            result.message.content or "", {rule.id for rule in rules}
        )
