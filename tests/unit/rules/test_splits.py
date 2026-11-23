"""Tests for cortex.rules.splits (#171)."""

from __future__ import annotations

import json
from uuid import UUID

import pytest

from cortex.rules.models import Rule, RuleOrigin, RuleSpec, RuleUse, UseOutcome
from cortex.rules.splits import SplitProposal, SplitProposer, _parse_split
from tests.unit.rules.fakes import llm_returning, sent_text

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
    llm = llm_returning(json.dumps(ANSWER))

    proposal = await SplitProposer(llm).propose(RULE, USES)

    sent = sent_text(llm)
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
    llm = llm_returning(json.dumps(ANSWER))

    await SplitProposer(llm).propose(RULE, USES)

    sent = sent_text(llm)
    assert str(RULE.id) not in sent
    assert "4711" not in sent
    assert "4712" not in sent


def test_one_child_is_refused() -> None:
    with pytest.raises(ValueError, match="exactly two children"):
        _parse_split(
            json.dumps({**ANSWER, "children": ANSWER["children"][:1]}), use_count=2
        )


def test_three_children_are_refused() -> None:
    children = ANSWER["children"] + [ANSWER["children"][1]]

    with pytest.raises(ValueError, match="exactly two children"):
        _parse_split(json.dumps({**ANSWER, "children": children}), use_count=2)


def test_a_child_with_a_blank_condition_is_refused() -> None:
    children = [{**ANSWER["children"][0], "condition": "  "}, ANSWER["children"][1]]

    with pytest.raises(ValueError):
        _parse_split(json.dumps({**ANSWER, "children": children}), use_count=2)


def test_a_child_with_a_blank_action_is_refused() -> None:
    children = [{**ANSWER["children"][0], "action": ""}, ANSWER["children"][1]]

    with pytest.raises(ValueError):
        _parse_split(json.dumps({**ANSWER, "children": children}), use_count=2)


def test_an_assignment_of_the_wrong_length_is_refused() -> None:
    with pytest.raises(ValueError):
        _parse_split(json.dumps({**ANSWER, "assignment": [0]}), use_count=2)
    with pytest.raises(ValueError):
        _parse_split(json.dumps({**ANSWER, "assignment": [0, 1, 0]}), use_count=2)


def test_an_assignment_that_names_no_child_is_refused() -> None:
    with pytest.raises(ValueError):
        _parse_split(json.dumps({**ANSWER, "assignment": [0, 2]}), use_count=2)


def test_a_boolean_is_not_a_child_index() -> None:
    with pytest.raises(ValueError):
        _parse_split(json.dumps({**ANSWER, "assignment": [True, False]}), use_count=2)


def test_a_malformed_answer_raises_value_error() -> None:
    with pytest.raises(ValueError):
        _parse_split("not json", use_count=2)


def test_a_fenced_or_think_prefixed_answer_is_parsed() -> None:
    plain = _parse_split(json.dumps(ANSWER), use_count=2)

    fenced = _parse_split("```json\n" + json.dumps(ANSWER) + "\n```", use_count=2)
    assert fenced == plain
    thought = _parse_split(
        "<think>cutting the rule</think>" + json.dumps(ANSWER), use_count=2
    )
    assert thought == plain


async def test_the_prompt_asks_for_disjoint_children_and_names_the_keeping_child_first() -> None:
    llm = llm_returning(json.dumps(ANSWER))

    await SplitProposer(llm).propose(RULE, USES)

    sent = sent_text(llm)
    assert "disjoint" in sent
    assert (
        "List first the child that keeps the current behaviour, and second the new, "
        "narrower rule."
    ) in sent
