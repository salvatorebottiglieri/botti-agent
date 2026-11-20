"""Tests for cortex.rules.feedback."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from cortex.llm.base import LLMClient
from cortex.llm.models import ChatMessage, ChatResult, Role
from cortex.rules.feedback import (
    AttributedUse,
    Feedback,
    FeedbackClassifier,
    RuleProposal,
    _parse_feedback,
)
from cortex.rules.models import Rule, RuleOrigin, RuleSpec, UseOutcome

RULE_A = UUID("11111111-1111-1111-1111-111111111111")
RULE_B = UUID("22222222-2222-2222-2222-222222222222")
RULE_C = UUID("33333333-3333-3333-3333-333333333333")


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


@pytest.mark.parametrize("reply", ["", "   ", "\n\t "])
async def test_blank_reply_makes_no_call(reply: str) -> None:
    llm = _llm_returning("{}")

    feedback = await FeedbackClassifier(llm).classify([_rule(RULE_A, "c", "a")], "turn", reply)

    assert feedback == Feedback()
    llm.chat.assert_not_awaited()


async def test_empty_rules_makes_no_call() -> None:
    llm = _llm_returning("{}")

    feedback = await FeedbackClassifier(llm).classify([], "turn", "a reply")

    assert feedback == Feedback()
    llm.chat.assert_not_awaited()


async def test_a_reasoning_turn_is_what_the_classifier_sends() -> None:
    llm = _llm_returning('{"uses": [], "proposals": []}')
    turn = "<think>weighing the options</think>here is the answer"

    await FeedbackClassifier(llm).classify([_rule(RULE_A, "when x", "do y")], turn, "fine")

    assert len(llm.chat.call_args.args[0]) == 3
    assert turn in _sent_text(llm)
    assert str(RULE_A) in _sent_text(llm)
    assert llm.chat.call_args.kwargs["generation_config"].temperature == 0
    llm.chat.assert_awaited_once()


async def test_a_think_prefixed_reply_is_parsed() -> None:
    body = json.dumps(
        {"uses": [{"rule_id": str(RULE_A), "outcome": "approval"}], "proposals": []}
    )
    llm = _llm_returning(f"<think>planning</think>{body}")

    feedback = await FeedbackClassifier(llm).classify(
        [_rule(RULE_A, "when x", "do y")], "turn", "ok"
    )

    assert feedback == Feedback(uses=[AttributedUse(rule_id=RULE_A, outcome=UseOutcome.APPROVAL)])


def test_one_use_per_rule_and_a_second_entry_for_the_same_rule_is_dropped() -> None:
    content = json.dumps(
        {
            "uses": [
                {"rule_id": str(RULE_A), "outcome": "approval"},
                {"rule_id": str(RULE_A), "outcome": "correction"},
            ],
            "proposals": [],
        }
    )

    feedback = _parse_feedback(content, {RULE_A})

    assert feedback.uses == [AttributedUse(rule_id=RULE_A, outcome=UseOutcome.APPROVAL)]


def test_an_invented_rule_id_is_dropped() -> None:
    content = json.dumps(
        {
            "uses": [
                {"rule_id": str(RULE_A), "outcome": "approval"},
                {"rule_id": str(RULE_B), "outcome": "approval"},
                {"rule_id": "not-a-uuid", "outcome": "approval"},
            ],
            "proposals": [],
        }
    )

    feedback = _parse_feedback(content, {RULE_A})

    assert [u.rule_id for u in feedback.uses] == [RULE_A]


def test_an_unknown_category_is_dropped() -> None:
    content = json.dumps(
        {
            "uses": [
                {"rule_id": str(RULE_A), "outcome": "approval"},
                {"rule_id": str(RULE_B), "outcome": "perhaps"},
            ],
            "proposals": [],
        }
    )

    feedback = _parse_feedback(content, {RULE_A, RULE_B})

    assert [u.rule_id for u in feedback.uses] == [RULE_A]


def test_only_the_first_proposal_survives() -> None:
    content = json.dumps(
        {
            "uses": [],
            "proposals": [
                {"condition": "when x", "action": "do y", "rationale": "r", "origin": "dictated"},
                {"condition": "when z", "action": "do w", "rationale": "r", "origin": "dictated"},
            ],
        }
    )

    feedback = _parse_feedback(content, set())

    assert len(feedback.proposals) == 1
    assert feedback.proposals[0].spec.condition == "when x"


def test_a_rule_in_context_but_not_applied_gets_no_entry() -> None:
    content = json.dumps(
        {"uses": [{"rule_id": str(RULE_A), "outcome": "neutral"}], "proposals": []}
    )

    feedback = _parse_feedback(content, {RULE_A, RULE_B, RULE_C})

    assert len(feedback.uses) == 1
    assert feedback.uses[0].rule_id == RULE_A


@pytest.mark.parametrize("content", ["", "not json", '{"proposals": []}', "[]"])
def test_malformed_json_raises_value_error(content: str) -> None:
    with pytest.raises(ValueError):
        _parse_feedback(content, {RULE_A})


def test_fenced_json_is_accepted() -> None:
    body = json.dumps(
        {"uses": [{"rule_id": str(RULE_A), "outcome": "correction"}], "proposals": []}
    )

    feedback = _parse_feedback(f"```json\n{body}\n```", {RULE_A})

    assert feedback.uses == [AttributedUse(rule_id=RULE_A, outcome=UseOutcome.CORRECTION)]


def test_a_proposal_with_blank_condition_or_action_is_dropped() -> None:
    content = json.dumps(
        {
            "uses": [],
            "proposals": [
                {"condition": "  ", "action": "do it", "rationale": "r", "origin": "dictated"},
                {"condition": "when x", "action": "", "rationale": "r", "origin": "dictated"},
                {"condition": "when x", "action": "do y", "rationale": "r", "origin": "dictated"},
            ],
        }
    )

    feedback = _parse_feedback(content, set())

    assert feedback.proposals == [
        RuleProposal(
            spec=RuleSpec(condition="when x", action="do y", rationale="r"),
            origin=RuleOrigin.DICTATED,
        )
    ]


@pytest.mark.parametrize("origin", ["merge", "", "nonsense", None])
def test_a_proposal_with_an_unknown_origin_defaults_to_correction(origin: object) -> None:
    content = json.dumps(
        {
            "uses": [],
            "proposals": [{"condition": "when x", "action": "do y", "rationale": "r", "origin": origin}],
        }
    )

    feedback = _parse_feedback(content, set())

    assert feedback.proposals == [
        RuleProposal(
            spec=RuleSpec(condition="when x", action="do y", rationale="r"),
            origin=RuleOrigin.CORRECTION,
        )
    ]
