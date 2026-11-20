"""AttributionRun tests (#168): one turn's Feedback becomes uses on its rules.

Driven through ``InMemoryRuleRepository``: a use row that was written is
visible, a rule that was refreshed is stored, and a failure that wrote nothing
leaves both untouched.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from cortex.rules.attribution import AttributionRun
from cortex.rules.feedback import AttributedUse, Feedback, RuleProposal
from cortex.rules.models import RuleOrigin, RuleSpec, RuleState, UseOutcome
from cortex.rules.service import RuleService
from tests.unit.rules.fakes import InMemoryRuleRepository

NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
SESSION = UUID("11111111-1111-4111-8111-111111111111")
SITUATION = "the user asks to book a table"

SPEC = RuleSpec(condition="the user asks to book a table", action="ask before booking")


class StubClassifier:
    """A classifier that returns what the test hands it, or raises."""

    def __init__(self, feedback: Feedback | None = None, error: Exception | None = None) -> None:
        self._feedback = feedback or Feedback()
        self._error = error

    async def classify(self, rules, assistant_turn, user_message) -> Feedback:
        if self._error is not None:
            raise self._error
        return self._feedback


class FailingRepository(InMemoryRuleRepository):
    """A repository whose write fails the way a dropped asyncpg connection does."""

    async def add_use(self, use):
        raise RuntimeError("connection lost")


class FailFirstWriteRepository(InMemoryRuleRepository):
    """A repository whose first ``add_use`` fails and whose later writes succeed."""

    def __init__(self) -> None:
        super().__init__()
        self._failed = False

    async def add_use(self, use):
        if not self._failed:
            self._failed = True
            raise RuntimeError("connection lost")
        return await super().add_use(use)


def _service(repository: InMemoryRuleRepository) -> RuleService:
    return RuleService(repository, clock=lambda: NOW)


async def test_the_run_writes_one_use_per_attributed_rule_through_the_service() -> None:
    repository = InMemoryRuleRepository()
    service = _service(repository)
    rule = await service.create_from_correction(SPEC)
    assert rule.state is RuleState.STAGING and rule.confidence == 0.5
    classifier = StubClassifier(
        Feedback(uses=[AttributedUse(rule_id=rule.id, outcome=UseOutcome.APPROVAL)])
    )

    refreshed = await AttributionRun(classifier, service).attribute(
        [rule], SESSION, "of course", "yes thanks", SITUATION
    )

    assert [
        (use.rule_id, use.session_id, use.situation, use.outcome) for use in repository.uses
    ] == [(rule.id, SESSION, SITUATION, UseOutcome.APPROVAL)]
    stored = await repository.get(rule.id)
    assert stored is not None
    assert stored.confidence == 20 / 21  # sigmoid(ln 20): one approval off the 0.5 prior
    assert refreshed == [stored]


async def test_the_run_writes_nothing_when_the_classifier_raises(caplog) -> None:
    repository = InMemoryRuleRepository()
    service = _service(repository)
    rule = await service.create_from_correction(SPEC)
    classifier = StubClassifier(error=ValueError("Malformed JSON from LLM"))

    with caplog.at_level(logging.WARNING):
        returned = await AttributionRun(classifier, service).attribute(
            [rule], SESSION, "turn", "a reply", SITUATION
        )

    assert returned is None
    assert repository.uses == []
    assert "attribution" in caplog.text.lower()


async def test_the_run_skips_a_rule_that_vanished(caplog) -> None:
    repository = InMemoryRuleRepository()
    service = _service(repository)
    rule = await service.create_from_correction(SPEC)
    ghost = uuid4()
    classifier = StubClassifier(
        Feedback(
            uses=[
                AttributedUse(rule_id=ghost, outcome=UseOutcome.APPROVAL),
                AttributedUse(rule_id=rule.id, outcome=UseOutcome.APPROVAL),
            ]
        )
    )

    with caplog.at_level(logging.WARNING):
        refreshed = await AttributionRun(classifier, service).attribute(
            [rule], SESSION, "turn", "a reply", SITUATION
        )

    assert [use.rule_id for use in repository.uses] == [rule.id]
    assert [stored.id for stored in refreshed] == [rule.id]
    assert str(ghost) in caplog.text


async def test_the_run_writes_nothing_when_the_repository_fails(caplog) -> None:
    repository = FailingRepository()
    service = _service(repository)
    rule = await service.create_from_correction(SPEC)
    classifier = StubClassifier(
        Feedback(uses=[AttributedUse(rule_id=rule.id, outcome=UseOutcome.APPROVAL)])
    )

    with caplog.at_level(logging.WARNING):
        refreshed = await AttributionRun(classifier, service).attribute(
            [rule], SESSION, "turn", "a reply", SITUATION
        )

    assert refreshed == [rule]
    assert repository.uses == []
    assert "connection lost" in caplog.text


async def test_the_run_leaves_proposals_alone() -> None:
    repository = InMemoryRuleRepository()
    service = _service(repository)
    rule = await service.create_from_correction(SPEC)
    classifier = StubClassifier(
        Feedback(
            proposals=[
                RuleProposal(
                    spec=RuleSpec(condition="the user asks for a table", action="ask for a time"),
                    origin=RuleOrigin.CORRECTION,
                )
            ]
        )
    )

    returned = await AttributionRun(classifier, service).attribute(
        [rule], SESSION, "turn", "a reply", SITUATION
    )

    assert list(repository.rules) == [rule.id]
    assert returned == [rule]


async def test_a_turn_with_no_attributed_uses_returns_the_rules_it_was_given() -> None:
    repository = InMemoryRuleRepository()
    service = _service(repository)
    rule = await service.create_from_correction(SPEC)

    returned = await AttributionRun(StubClassifier(Feedback()), service).attribute(
        [rule], SESSION, "turn", "a reply", SITUATION
    )

    assert returned == [rule]
    assert repository.uses == []


async def test_a_failed_write_does_not_stop_the_next_use(caplog) -> None:
    repository = FailFirstWriteRepository()
    service = _service(repository)
    first = await service.create_from_correction(SPEC)
    second = await service.create_from_correction(SPEC)
    classifier = StubClassifier(
        Feedback(
            uses=[
                AttributedUse(rule_id=first.id, outcome=UseOutcome.APPROVAL),
                AttributedUse(rule_id=second.id, outcome=UseOutcome.APPROVAL),
            ]
        )
    )

    with caplog.at_level(logging.WARNING):
        refreshed = await AttributionRun(classifier, service).attribute(
            [first, second], SESSION, "turn", "a reply", SITUATION
        )

    assert [use.rule_id for use in repository.uses] == [second.id]
    assert [stored.id for stored in refreshed] == [second.id]
    assert str(first.id) in caplog.text


SECOND_SESSION = UUID("22222222-2222-4222-8222-222222222222")


async def test_the_run_stamps_every_use_with_the_queued_turns_id() -> None:
    """Witness: an unstamped use lets the same turn score a rule twice."""
    repository = InMemoryRuleRepository()
    service = _service(repository)
    first = await service.create_from_correction(SPEC)
    second = await service.create_from_correction(SPEC)
    feedback_id = uuid4()
    classifier = StubClassifier(
        Feedback(
            uses=[
                AttributedUse(rule_id=first.id, outcome=UseOutcome.APPROVAL),
                AttributedUse(rule_id=second.id, outcome=UseOutcome.APPROVAL),
            ]
        )
    )

    await AttributionRun(classifier, service).attribute(
        [first, second], SESSION, "turn", "a reply", SITUATION, feedback_id=feedback_id
    )

    assert [use.feedback_id for use in repository.uses] == [feedback_id, feedback_id]


async def test_three_approvals_from_two_sessions_promote_a_staging_rule() -> None:
    repository = InMemoryRuleRepository()
    service = _service(repository)
    rule = await service.create_from_correction(SPEC)
    classifier = StubClassifier(
        Feedback(uses=[AttributedUse(rule_id=rule.id, outcome=UseOutcome.APPROVAL)])
    )
    run = AttributionRun(classifier, service)

    await run.attribute([rule], SESSION, "turn", "yes", SITUATION)
    after_one = await repository.get(rule.id)
    assert after_one is not None
    # One approval moves the confidence; the state needs both promotion counters.
    assert after_one.confidence == 20 / 21
    assert after_one.state is RuleState.STAGING

    await run.attribute([after_one], SECOND_SESSION, "turn", "yes", SITUATION)
    after_two = await repository.get(rule.id)
    assert after_two is not None
    assert after_two.state is RuleState.STAGING

    await run.attribute([after_two], SESSION, "turn", "yes", SITUATION)
    after_three = await repository.get(rule.id)
    assert after_three is not None
    assert after_three.state is RuleState.DEFINITIVE
    assert len(repository.uses) == 3
