"""AttributionRun tests (#168): one turn's Feedback becomes uses on its rules.

Driven through ``InMemoryRuleRepository``: a use row that was written is
visible, a rule that was refreshed is stored, and a failure that wrote nothing
leaves both untouched.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from cortex.rules.attribution import AttributionRun
from cortex.rules.constants import CONTEXT_BUDGET
from cortex.rules.feedback import AttributedUse, Feedback, RuleProposal
from cortex.rules.models import Rule, RuleOrigin, RuleSpec, RuleState, RuleUse, UseOutcome
from cortex.rules.service import RuleService
from cortex.rules.splits import SplitProposal
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


class StubConflictCheck:
    """A conflict check that answers what the test hands it, or raises."""

    def __init__(self, contradicts: bool = False, error: Exception | None = None) -> None:
        self._contradicts = contradicts
        self._error = error
        self.calls: list[tuple[RuleSpec, list]] = []

    async def contradicts(self, candidate, active) -> bool:
        self.calls.append((candidate, list(active)))
        if self._error is not None:
            raise self._error
        return self._contradicts


PROPOSAL = RuleSpec(
    condition="the user asks for a table", action="ask for a time", rationale="they said so"
)


async def test_a_dictated_proposal_is_stored_definitive_with_the_dictation_as_its_first_use() -> None:
    repository = InMemoryRuleRepository()
    service = _service(repository)
    existing = await service.create_from_correction(SPEC)
    classifier = StubClassifier(
        Feedback(proposals=[RuleProposal(spec=PROPOSAL, origin=RuleOrigin.DICTATED)])
    )

    returned = await AttributionRun(classifier, service, StubConflictCheck()).attribute(
        [existing], SESSION, "turn", "a reply", SITUATION
    )

    created = [stored for stored in repository.rules.values() if stored.id != existing.id]
    assert len(created) == 1
    rule = created[0]
    assert rule.state is RuleState.DEFINITIVE
    assert rule.confidence == 20 / 21
    assert rule.origin is RuleOrigin.DICTATED
    assert [(use.rule_id, use.session_id, use.situation, use.outcome) for use in repository.uses] == [
        (rule.id, SESSION, PROPOSAL.condition, UseOutcome.APPROVAL)
    ]
    assert rule.id in [stored.id for stored in returned]


async def test_a_correction_proposal_is_stored_in_staging() -> None:
    repository = InMemoryRuleRepository()
    service = _service(repository)
    existing = await service.create_from_correction(SPEC)
    classifier = StubClassifier(
        Feedback(proposals=[RuleProposal(spec=PROPOSAL, origin=RuleOrigin.CORRECTION)])
    )

    returned = await AttributionRun(classifier, service, StubConflictCheck()).attribute(
        [existing], SESSION, "turn", "a reply", SITUATION
    )

    created = [stored for stored in repository.rules.values() if stored.id != existing.id]
    assert len(created) == 1
    assert created[0].state is RuleState.STAGING
    assert created[0].confidence == 0.5
    assert created[0].origin is RuleOrigin.CORRECTION
    assert created[0].id in [stored.id for stored in returned]


async def test_a_dictated_proposal_replaces_the_rule_it_names() -> None:
    """Witness: dropping `replaces` creates a second active rule beside the one
    the user superseded, instead of archiving it."""
    repository = InMemoryRuleRepository()
    service = _service(repository)
    predecessor = await service.create_dictated(SESSION, SPEC)
    classifier = StubClassifier(
        Feedback(
            proposals=[
                RuleProposal(
                    spec=PROPOSAL, origin=RuleOrigin.DICTATED, replaces=predecessor.id
                )
            ]
        )
    )

    await AttributionRun(classifier, service, StubConflictCheck()).attribute(
        [predecessor], SESSION, "turn", "a reply", SITUATION
    )

    superseded = await repository.get(predecessor.id)
    assert superseded is not None
    assert superseded.state is RuleState.ARCHIVED
    created = [stored for stored in repository.rules.values() if stored.id != predecessor.id]
    assert len(created) == 1
    assert created[0].state is RuleState.DEFINITIVE
    assert superseded.superseded_by == created[0].id


async def test_a_dictation_naming_an_archived_rule_still_creates_its_rule(caplog) -> None:
    """Witness: a refused replacement costs a duplicate, never the user's rule."""
    repository = InMemoryRuleRepository()
    service = _service(repository)
    predecessor = await service.create_dictated(SESSION, SPEC)
    archived = await service.archive(predecessor.id)
    assert archived is not None and archived.state is RuleState.ARCHIVED
    classifier = StubClassifier(
        Feedback(
            proposals=[
                RuleProposal(
                    spec=PROPOSAL, origin=RuleOrigin.DICTATED, replaces=predecessor.id
                )
            ]
        )
    )

    with caplog.at_level(logging.WARNING):
        returned = await AttributionRun(classifier, service, StubConflictCheck()).attribute(
            [archived], SESSION, "turn", "a reply", SITUATION
        )

    retained = await repository.get(predecessor.id)
    assert retained is not None
    assert retained.state is RuleState.ARCHIVED
    assert retained.superseded_by is None
    created = [stored for stored in repository.rules.values() if stored.id != predecessor.id]
    assert len(created) == 1
    assert created[0].state is RuleState.DEFINITIVE
    assert created[0].id in [stored.id for stored in returned]
    assert str(predecessor.id) in caplog.text


async def test_a_dictation_naming_a_rule_that_does_not_exist_still_creates_its_rule(caplog) -> None:
    """Witness: the same for a replacement that vanished before the write."""
    repository = InMemoryRuleRepository()
    service = _service(repository)
    existing = await service.create_from_correction(SPEC)
    ghost = uuid4()
    classifier = StubClassifier(
        Feedback(
            proposals=[RuleProposal(spec=PROPOSAL, origin=RuleOrigin.DICTATED, replaces=ghost)]
        )
    )

    with caplog.at_level(logging.WARNING):
        returned = await AttributionRun(classifier, service, StubConflictCheck()).attribute(
            [existing], SESSION, "turn", "a reply", SITUATION
        )

    retained = await repository.get(existing.id)
    assert retained is not None
    assert retained.superseded_by is None
    created = [stored for stored in repository.rules.values() if stored.id != existing.id]
    assert len(created) == 1
    assert created[0].state is RuleState.DEFINITIVE
    assert created[0].id in [stored.id for stored in returned]
    assert str(ghost) in caplog.text


async def test_a_correction_proposal_ignores_the_replaces_it_carries() -> None:
    """Witness: a correction creates a rule that was missing; it never rewrites
    one, so the predecessor must stay active."""
    repository = InMemoryRuleRepository()
    service = _service(repository)
    predecessor = await service.create_dictated(SESSION, SPEC)
    classifier = StubClassifier(
        Feedback(
            proposals=[
                RuleProposal(
                    spec=PROPOSAL, origin=RuleOrigin.CORRECTION, replaces=predecessor.id
                )
            ]
        )
    )

    await AttributionRun(classifier, service, StubConflictCheck()).attribute(
        [predecessor], SESSION, "turn", "a reply", SITUATION
    )

    retained = await repository.get(predecessor.id)
    assert retained is not None
    assert retained.state is RuleState.DEFINITIVE
    assert retained.superseded_by is None
    created = [stored for stored in repository.rules.values() if stored.id != predecessor.id]
    assert len(created) == 1
    assert created[0].state is RuleState.STAGING


async def test_a_contradicting_proposal_is_not_stored_and_the_uses_stand(caplog) -> None:
    repository = InMemoryRuleRepository()
    service = _service(repository)
    rule = await service.create_from_correction(SPEC)
    classifier = StubClassifier(
        Feedback(
            uses=[AttributedUse(rule_id=rule.id, outcome=UseOutcome.APPROVAL)],
            proposals=[RuleProposal(spec=PROPOSAL, origin=RuleOrigin.DICTATED)],
        )
    )

    with caplog.at_level(logging.INFO):
        returned = await AttributionRun(classifier, service, StubConflictCheck(True)).attribute(
            [rule], SESSION, "turn", "a reply", SITUATION
        )

    assert list(repository.rules) == [rule.id]
    assert [use.rule_id for use in repository.uses] == [rule.id]
    assert [stored.id for stored in returned] == [rule.id]
    assert PROPOSAL.condition in caplog.text


async def test_a_failing_conflict_check_drops_the_proposal_and_keeps_the_uses(caplog) -> None:
    repository = InMemoryRuleRepository()
    service = _service(repository)
    rule = await service.create_from_correction(SPEC)
    classifier = StubClassifier(
        Feedback(
            uses=[AttributedUse(rule_id=rule.id, outcome=UseOutcome.APPROVAL)],
            proposals=[RuleProposal(spec=PROPOSAL, origin=RuleOrigin.DICTATED)],
        )
    )
    check = StubConflictCheck(error=ValueError("Malformed JSON from LLM"))

    with caplog.at_level(logging.WARNING):
        returned = await AttributionRun(classifier, service, check).attribute(
            [rule], SESSION, "turn", "a reply", SITUATION
        )

    assert list(repository.rules) == [rule.id]
    assert [use.rule_id for use in repository.uses] == [rule.id]
    assert [stored.id for stored in returned] == [rule.id]
    assert "Malformed JSON from LLM" in caplog.text


async def test_the_check_sees_the_active_rules_at_the_context_budget() -> None:
    repository = InMemoryRuleRepository()
    service = _service(repository)
    active_rules = [
        await service.create_dictated(SESSION, RuleSpec(condition=f"condition {index}", action="act"))
        for index in range(CONTEXT_BUDGET + 1)
    ]
    classifier = StubClassifier(
        Feedback(proposals=[RuleProposal(spec=PROPOSAL, origin=RuleOrigin.DICTATED)])
    )
    check = StubConflictCheck()

    await AttributionRun(classifier, service, check).attribute(
        [active_rules[0]], SESSION, "turn", "a reply", SITUATION
    )

    candidate, active = check.calls[0]
    assert candidate == PROPOSAL
    assert len(active) == CONTEXT_BUDGET
    assert {stored.id for stored in active} <= {rule.id for rule in active_rules}


async def test_the_check_is_never_called_without_a_proposal() -> None:
    """Witness: a check run on every turn pays a provider call for nothing."""
    repository = InMemoryRuleRepository()
    service = _service(repository)
    rule = await service.create_from_correction(SPEC)
    classifier = StubClassifier(
        Feedback(uses=[AttributedUse(rule_id=rule.id, outcome=UseOutcome.APPROVAL)])
    )
    check = StubConflictCheck()

    await AttributionRun(classifier, service, check).attribute(
        [rule], SESSION, "turn", "a reply", SITUATION
    )

    assert check.calls == []


async def test_only_the_first_proposal_is_created_and_the_dropped_one_is_logged(caplog) -> None:
    repository = InMemoryRuleRepository()
    service = _service(repository)
    existing = await service.create_from_correction(SPEC)
    second = RuleSpec(condition="the user asks for a bill", action="ask for a tip")
    classifier = StubClassifier(
        Feedback(
            proposals=[
                RuleProposal(spec=PROPOSAL, origin=RuleOrigin.DICTATED),
                RuleProposal(spec=second, origin=RuleOrigin.DICTATED),
            ]
        )
    )

    with caplog.at_level(logging.WARNING):
        await AttributionRun(classifier, service, StubConflictCheck()).attribute(
            [existing], SESSION, "turn", "a reply", SITUATION
        )

    created = [stored for stored in repository.rules.values() if stored.id != existing.id]
    assert [rule.condition for rule in created] == [PROPOSAL.condition]
    assert "2 proposals" in caplog.text


SECOND_SPEC = RuleSpec(condition="the user asks for a bill", action="ask for a tip")

TWO_CHILDREN = (
    RuleSpec(condition="a dinner booking", action="confirm first"),
    RuleSpec(condition="a pizza order", action="order directly"),
)


class StubSplitProposer:
    """A split proposer that records its calls, answers two children, or raises.

    Its assignment names one child per use it is handed, exactly as the real
    proposer's does: a use left unassigned is a use the service refuses.
    """

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.calls: list[tuple[Rule, list[RuleUse]]] = []

    async def propose(self, rule: Rule, uses: Sequence[RuleUse]) -> SplitProposal:
        self.calls.append((rule, list(uses)))
        if self._error is not None:
            raise self._error
        return SplitProposal(
            children=TWO_CHILDREN,
            assignment=tuple(index % 2 for index in range(len(uses))),
        )


async def test_a_scope_correction_splits_the_rule_and_redistributes_its_uses() -> None:
    repository = InMemoryRuleRepository()
    service = _service(repository)
    rule = await service.create_from_correction(SPEC)
    await service.record_use(rule.id, SESSION, "a dinner for two", UseOutcome.APPROVAL)
    await service.record_use(rule.id, SECOND_SESSION, "a pizza slice", UseOutcome.IMPLICIT)
    classifier = StubClassifier(
        Feedback(uses=[AttributedUse(rule_id=rule.id, outcome=UseOutcome.SCOPE_CORRECTION)])
    )
    proposer = StubSplitProposer()

    returned = await AttributionRun(classifier, service, splits=proposer).attribute(
        [rule], SESSION, "turn", "no, I meant a booking", SITUATION
    )

    parent = await repository.get(rule.id)
    assert parent is not None
    assert parent.state is RuleState.ARCHIVED
    children = await service.list_children(rule.id)
    assert [child.parent_id for child in children] == [rule.id, rule.id]
    inherited_by_first = next(
        child.id for child in children if child.condition == TWO_CHILDREN[0].condition
    )
    assert parent.superseded_by == inherited_by_first
    # Every use of the parent moved to exactly one child: a use left on the
    # archived parent or copied onto both would score a rule that is gone.
    per_child = {child.id: await service.list_uses(child.id) for child in children}
    assert len(per_child) == 2
    assert all(uses for uses in per_child.values())
    assert sum(len(uses) for uses in per_child.values()) == len(repository.uses) == 3
    assert [use.id for use in repository.uses if use.rule_id == rule.id] == []
    assert [len(uses) for _, uses in proposer.calls] == [3]
    assert {child.id for child in children} <= {stored.id for stored in returned}


async def test_an_archived_rule_is_not_split() -> None:
    repository = InMemoryRuleRepository()
    service = _service(repository)
    rule = await service.create_from_correction(SPEC)
    await service.record_use(rule.id, SESSION, "a dinner for two", UseOutcome.APPROVAL)
    await service.record_use(rule.id, SECOND_SESSION, "a pizza slice", UseOutcome.IMPLICIT)
    await service.archive(rule.id)
    classifier = StubClassifier(
        Feedback(uses=[AttributedUse(rule_id=rule.id, outcome=UseOutcome.SCOPE_CORRECTION)])
    )
    proposer = StubSplitProposer()

    returned = await AttributionRun(classifier, service, splits=proposer).attribute(
        [rule], SESSION, "turn", "no, I meant a booking", SITUATION
    )

    assert proposer.calls == []
    assert await service.list_children(rule.id) == []
    stored = await repository.get(rule.id)
    assert stored is not None
    assert stored.state is RuleState.ARCHIVED
    assert returned is not None


async def test_a_scope_correction_without_a_proposer_records_the_use_and_does_not_split() -> None:
    repository = InMemoryRuleRepository()
    service = _service(repository)
    rule = await service.create_from_correction(SPEC)
    classifier = StubClassifier(
        Feedback(uses=[AttributedUse(rule_id=rule.id, outcome=UseOutcome.SCOPE_CORRECTION)])
    )

    await AttributionRun(classifier, service).attribute(
        [rule], SESSION, "turn", "no, I meant a booking", SITUATION
    )

    assert [(use.rule_id, use.outcome) for use in repository.uses] == [
        (rule.id, UseOutcome.SCOPE_CORRECTION)
    ]
    stored = await repository.get(rule.id)
    assert stored is not None
    assert stored.state is RuleState.STAGING
    assert await service.list_children(rule.id) == []


async def test_a_plain_correction_never_asks_the_proposer() -> None:
    """Witness: a split on every correction pays a provider call for nothing."""
    repository = InMemoryRuleRepository()
    service = _service(repository)
    rule = await service.create_from_correction(SPEC)
    classifier = StubClassifier(
        Feedback(uses=[AttributedUse(rule_id=rule.id, outcome=UseOutcome.CORRECTION)])
    )
    proposer = StubSplitProposer()

    await AttributionRun(classifier, service, splits=proposer).attribute(
        [rule], SESSION, "turn", "no, ask for a time", SITUATION
    )

    assert proposer.calls == []
    assert await service.list_children(rule.id) == []


async def test_a_failing_proposer_leaves_the_parent_active_with_its_use_written(caplog) -> None:
    repository = InMemoryRuleRepository()
    service = _service(repository)
    rule = await service.create_from_correction(SPEC)
    classifier = StubClassifier(
        Feedback(uses=[AttributedUse(rule_id=rule.id, outcome=UseOutcome.SCOPE_CORRECTION)])
    )
    proposer = StubSplitProposer(error=ValueError("Malformed JSON from LLM"))

    with caplog.at_level(logging.WARNING):
        returned = await AttributionRun(classifier, service, splits=proposer).attribute(
            [rule], SESSION, "turn", "no, I meant a booking", SITUATION
        )

    assert [use.outcome for use in repository.uses] == [UseOutcome.SCOPE_CORRECTION]
    stored = await repository.get(rule.id)
    assert stored is not None
    assert stored.state in (RuleState.STAGING, RuleState.DEFINITIVE)
    assert stored.superseded_by is None
    assert await service.list_children(rule.id) == []
    assert [refreshed.id for refreshed in returned] == [rule.id]
    assert "Malformed JSON from LLM" in caplog.text


async def test_a_failing_split_does_not_cancel_the_other_uses() -> None:
    repository = InMemoryRuleRepository()
    service = _service(repository)
    split_parent = await service.create_from_correction(SPEC)
    corrected = await service.create_from_correction(SECOND_SPEC)
    classifier = StubClassifier(
        Feedback(
            uses=[
                AttributedUse(rule_id=split_parent.id, outcome=UseOutcome.SCOPE_CORRECTION),
                AttributedUse(rule_id=corrected.id, outcome=UseOutcome.CORRECTION),
            ]
        )
    )
    proposer = StubSplitProposer(error=ValueError("Malformed JSON from LLM"))

    await AttributionRun(classifier, service, splits=proposer).attribute(
        [split_parent, corrected], SESSION, "turn", "no, I meant a booking", SITUATION
    )

    assert {(use.rule_id, use.outcome) for use in repository.uses} == {
        (split_parent.id, UseOutcome.SCOPE_CORRECTION),
        (corrected.id, UseOutcome.CORRECTION),
    }
    assert await service.list_children(split_parent.id) == []


async def test_the_returned_parent_is_the_archived_one() -> None:
    repository = InMemoryRuleRepository()
    service = _service(repository)
    rule = await service.create_from_correction(SPEC)
    await service.record_use(rule.id, SESSION, "a dinner for two", UseOutcome.APPROVAL)
    await service.record_use(rule.id, SECOND_SESSION, "a pizza slice", UseOutcome.IMPLICIT)
    classifier = StubClassifier(
        Feedback(uses=[AttributedUse(rule_id=rule.id, outcome=UseOutcome.SCOPE_CORRECTION)])
    )
    proposer = StubSplitProposer()

    returned = await AttributionRun(classifier, service, splits=proposer).attribute(
        [rule], SESSION, "turn", "no, I meant a booking", SITUATION
    )

    parents = [stored for stored in returned if stored.id == rule.id]
    assert len(parents) == 1
    assert parents[0].state is RuleState.ARCHIVED
    assert parents[0].superseded_by is not None
    children = [stored for stored in returned if stored.id != rule.id]
    assert len(children) == 2
    assert {child.parent_id for child in children} == {rule.id}
