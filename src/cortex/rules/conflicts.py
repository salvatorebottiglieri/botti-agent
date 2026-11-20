"""The conflict check standing between a proposed rule and the store (#170).

One ``chat()`` comparing the candidate with the active set, one tolerant parse.
A candidate may not enter the store next to a rule it could not be followed
alongside.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from cortex.llm.base import LLMClient
from cortex.llm.config import GenerationConfig
from cortex.llm.models import ChatMessage, Role
from cortex.rules.feedback import strip_json_wrapping
from cortex.rules.models import Rule, RuleSpec

_SYSTEM_PROMPT = (
    "You compare a candidate rule with the rules already in force.\n"
    "The active rules are listed one per line as <uuid> | <condition> → <action>.\n"
    "The candidate is the line starting with 'candidate'.\n"
    "Answer true when the candidate and an active rule could not both be followed in the\n"
    "same situation, false otherwise. Answer with JSON of the shape\n"
    '{"contradicts": true | false, "reason": "..."}; no markdown.'
)


def _parse_verdict(content: str) -> bool:
    """Parse the JSON verdict. Raises ``ValueError`` on malformed JSON or a
    missing ``contradicts`` key."""
    try:
        data = json.loads(strip_json_wrapping(content))
    except json.JSONDecodeError as e:
        raise ValueError(f"Malformed JSON from LLM: {e}") from e
    if not isinstance(data, dict) or "contradicts" not in data:
        raise ValueError("LLM JSON must be an object with a 'contradicts' key")
    return data["contradicts"] is True


class ConflictCheck:
    """Decides whether a proposed rule contradicts the active set."""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm_client = llm_client
        # A verdict, not a generation: temperature 0.
        self._generation = GenerationConfig(temperature=0.0)

    async def contradicts(self, candidate: RuleSpec, active: Sequence[Rule]) -> bool:
        """True when the candidate cannot be followed alongside an active rule.

        An empty active set contradicts nothing, and costs no provider call.
        """
        if not active:
            return False
        listed = "\n".join(f"{rule.id} | {rule.condition} → {rule.action}" for rule in active)
        messages = [
            ChatMessage(
                role=Role.SYSTEM,
                content=(
                    f"{_SYSTEM_PROMPT}\nActive rules:\n{listed}\n"
                    f"candidate | {candidate.condition} → {candidate.action}\n"
                    f"candidate rationale: {candidate.rationale}"
                ),
            )
        ]
        result = await self._llm_client.chat(messages, generation_config=self._generation)
        return _parse_verdict(result.message.content or "")
