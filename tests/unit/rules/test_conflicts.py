"""Tests for cortex.rules.conflicts."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from cortex.llm.base import LLMClient
from cortex.llm.models import ChatMessage, ChatResult, Role
from cortex.rules.conflicts import ConflictCheck
from cortex.rules.models import Rule, RuleOrigin, RuleSpec

RULE_A = UUID("11111111-1111-1111-1111-111111111111")
RULE_B = UUID("22222222-2222-2222-2222-222222222222")

CANDIDATE = RuleSpec(condition="the user asks to book", action="book silently", rationale="they said so")


def _rule(rule_id: UUID, condition: str, action: str) -> Rule:
    return Rule(id=rule_id, condition=condition, action=action, origin=RuleOrigin.DICTATED)


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


async def test_an_empty_active_set_contradicts_nothing_and_makes_no_call() -> None:
    llm = _llm_returning('{"contradicts": true, "reason": "x"}')

    contradicts = await ConflictCheck(llm).contradicts(CANDIDATE, [])

    assert contradicts is False
    llm.chat.assert_not_awaited()


@pytest.mark.parametrize("answer,expected", [(True, True), (False, False)])
async def test_the_verdict_is_the_answer(answer: bool, expected: bool) -> None:
    llm = _llm_returning(json.dumps({"contradicts": answer, "reason": "they can both hold"}))

    contradicts = await ConflictCheck(llm).contradicts(CANDIDATE, [_rule(RULE_A, "ask first", "ask")])

    assert contradicts is expected


async def test_the_active_rules_and_the_candidate_reach_the_prompt() -> None:
    llm = _llm_returning('{"contradicts": false, "reason": "fine"}')

    await ConflictCheck(llm).contradicts(
        CANDIDATE, [_rule(RULE_A, "ask first", "ask"), _rule(RULE_B, "confirm", "confirm")]
    )

    sent = _sent_text(llm)
    assert str(RULE_A) in sent
    assert str(RULE_B) in sent
    assert CANDIDATE.condition in sent
    assert CANDIDATE.action in sent
    assert CANDIDATE.rationale in sent
    assert llm.chat.call_args.kwargs["generation_config"].temperature == 0
    llm.chat.assert_awaited_once()


async def test_a_think_prefixed_answer_is_parsed() -> None:
    body = json.dumps({"contradicts": True, "reason": "they conflict"})
    llm = _llm_returning(f"<think>comparing</think>{body}")

    contradicts = await ConflictCheck(llm).contradicts(CANDIDATE, [_rule(RULE_A, "ask", "ask")])

    assert contradicts is True


@pytest.mark.parametrize("content", ["", "not json", '{"reason": "x"}', "[]"])
async def test_malformed_json_raises_value_error(content: str) -> None:
    llm = _llm_returning(content)

    with pytest.raises(ValueError):
        await ConflictCheck(llm).contradicts(CANDIDATE, [_rule(RULE_A, "ask", "ask")])
