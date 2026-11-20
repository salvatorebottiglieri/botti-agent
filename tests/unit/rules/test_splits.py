"""Tests for cortex.rules.splits (#171)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from cortex.llm.base import LLMClient
from cortex.llm.models import ChatMessage, ChatResult, Role
from cortex.rules.models import Rule, RuleOrigin, RuleSpec, RuleUse, UseOutcome
from cortex.rules.splits import SplitProposal, SplitProposer, _parse_split

RULE = Rule(
    id=UUID("44444444-4444-4444-8444-444444444444"),
    condition="the user asks to book a table",
    action="ask before booking",
    origin=RuleOrigin.DICTATED,
)
SESSION = UUID("22222222-2222-4222-8222-222222222222")

USES = [
    RuleUse(
        rule_id=RULE.id,
        session_id=SESSION,
        situation="a dinner for two",
        outcome=UseOutcome.APPROVAL,
        id=4711,
    ),
    RuleUse(
        rule_id=RULE.id,
        session_id=SESSION,
        situation="a pizza slice",
        outcome=UseOutcome.IMPLICIT,
        id=4712,
    ),
]

ANSWER = {
    "children": [
        {
            "condition": "a dinner booking",
            "action": "confirm first",
            "rationale": "they said so",
        },
        {"condition": "a pizza order", "action": "order directly", "rationale": ""},
    ],
    "assignment": [0, 1],
}


def _llm_returning(content: str | None) -> MagicMock:
    """An LLM client stub whose chat() returns ``content``."""
    llm = MagicMock(spec=LLMClient)
    llm.chat = AsyncMock(
        return_value=ChatResult(message=ChatMessage(role=Role.ASSISTANT, content=content))
    )
    return llm


def _sent_text(llm: MagicMock) -> str:
    messages: list[ChatMessage] = llm.chat.call_args.args[0]
    return "\n".join(m.content or "" for m in messages)


def test_two_children_and_a_positional_assignment_are_parsed() -> None:
    proposal = _parse_split(json.dumps(ANSWER), use_count=2)

    assert proposal == SplitProposal(
        children=(
            RuleSpec(
                condition="a dinner booking",
                action="confirm first",
                rationale="they said so",
            ),
            RuleSpec(condition="a pizza order", action="order directly", rationale=""),
        ),
        assignment=(0, 1),
    )


async def test_the_request_lists_every_use_with_its_index_and_situation() -> None:
    llm = _llm_returning(json.dumps(ANSWER))

    proposal = await SplitProposer(llm).propose(RULE, USES)

    sent = _sent_text(llm)
    assert RULE.condition in sent
    assert RULE.action in sent
    assert "0 | a dinner for two → approval" in sent
    assert "1 | a pizza slice → implicit" in sent
    assert proposal.assignment == (0, 1)
    assert llm.chat.call_args.kwargs["generation_config"].temperature == 0
    llm.chat.assert_awaited_once()


async def test_no_rule_or_use_id_reaches_the_request() -> None:
    """Witness: an id-keyed prompt asks the model to echo an id it never saw;
    the assignment is positional precisely so no id has to round-trip."""
    llm = _llm_returning(json.dumps(ANSWER))

    await SplitProposer(llm).propose(RULE, USES)

    sent = _sent_text(llm)
    assert str(RULE.id) not in sent
    assert "4711" not in sent
    assert "4712" not in sent
