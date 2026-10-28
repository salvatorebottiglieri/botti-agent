"""RuleService lifecycle tests (docs/specs/rule-system.md ## Lifecycle).

Driven through ``InMemoryRuleRepository`` so the tests see what was *stored*:
a confidence that is mutated but not refreshed through the repository is
invisible, which is how the "always recomputed and persisted" invariant is
falsified.

Each test names its WITNESS: the plausible wrong implementation it separates
from the correct one.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from cortex.rules.constants import CONTEXT_BUDGET, VALIDITY_WINDOW
from cortex.rules.models import (
    Rule,
    RuleNotFoundError,
    RuleOrigin,
    RuleSpec,
    RuleState,
    RuleUse,
    UseOutcome,
)
from cortex.rules.render import render_rules
from cortex.rules.service import RuleService
from tests.unit.rules.fakes import InMemoryRuleRepository

NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)

SESSION_A = UUID("11111111-1111-4111-8111-111111111111")
SESSION_B = UUID("22222222-2222-4222-8222-222222222222")
SESSION_C = UUID("33333333-3333-4333-8333-333333333333")

SPEC = RuleSpec(
    condition="the user asks to book a table",
    action="ask for confirmation before booking",
    rationale="she was charged for a restaurant she did not choose",
)

ONE_APPROVAL = 20 / 21
TWO_APPROVALS = 400 / 401
THREE_APPROVALS = 8000 / 8001


class FakeClock:
    """A settable clock: `advance()` moves the rule system's now."""

    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> datetime:
        self.now += delta
        return self.now


def _service() -> tuple[RuleService, InMemoryRuleRepository, FakeClock]:
    repository = InMemoryRuleRepository()
    clock = FakeClock()
    return RuleService(repository, clock=clock), repository, clock


async def _stored_uses(repository: InMemoryRuleRepository, rule_id: UUID) -> list[RuleUse]:
    return [use for use in repository.uses if use.rule_id == rule_id]


class TestCreateDictated:
    async def test_dictated_rule_is_born_definitive_off_its_dictation_use(self):
        """Witness: with a plain 0.5 prior and no dictation row the rule would
        be at 0.5, below the 0.7 demotion threshold, and the first sweep would
        demote it — the spec explicitly rules that out."""
        service, repository, _ = _service()

        rule = await service.create_dictated(SESSION_A, SPEC)

        assert rule.state is RuleState.DEFINITIVE
        assert rule.origin is RuleOrigin.DICTATED
        assert rule.confidence == pytest.approx(ONE_APPROVAL)
        uses = await _stored_uses(repository, rule.id)
        assert len(uses) == 1
        assert uses[0].outcome is UseOutcome.APPROVAL
        assert uses[0].session_id == SESSION_A

        # The dictation survives the first sweep: not demoted to staging.
        active = await service.list_active()
        assert [(r.id, r.state) for r in active] == [(rule.id, RuleState.DEFINITIVE)]
        assert active[0].confidence == pytest.approx(ONE_APPROVAL)

    async def test_dictated_rule_expires_with_its_dictation(self):
        """Witness: a dictation saved as a permanent prior never moves; here the
        6-month window takes it back to 0.5 and demotes it to staging."""
        service, _, clock = _service()
        rule = await service.create_dictated(SESSION_A, SPEC)

        clock.advance(VALIDITY_WINDOW + timedelta(days=1))
        active = await service.list_active()

        assert len(active) == 1
        assert active[0].id == rule.id
        assert active[0].state is RuleState.STAGING
        assert active[0].confidence == pytest.approx(0.5)

    async def test_blank_condition_or_action_is_rejected_before_any_write(self):
        """Witness: an implementation validating `is None` accepts whitespace and
        stores a rule that can never be applied."""
        service, repository, _ = _service()

        with pytest.raises(ValueError):
            await service.create_dictated(SESSION_A, RuleSpec(condition="   ", action="x"))
        with pytest.raises(ValueError):
            await service.create_dictated(SESSION_A, RuleSpec(condition="x", action=""))

        assert repository.rules == {}
        assert repository.uses == []


class TestCreateFromCorrection:
    async def test_correction_born_rule_starts_in_staging_with_no_use(self):
        """Witness: reusing the dictation path would birth it definitive at 20/21."""
        service, repository, _ = _service()

        rule = await service.create_from_correction(SESSION_A, SPEC)

        assert rule.state is RuleState.STAGING
        assert rule.confidence == pytest.approx(0.5)
        assert rule.origin is RuleOrigin.CORRECTION
        assert await _stored_uses(repository, rule.id) == []

    async def test_correction_born_rule_is_not_archived_on_the_first_sweep(self):
        """Witness: 0.5 is above the archive threshold 0.3, so a fresh rule is
        visible in staging rather than retired."""
        service, _, _ = _service()
        rule = await service.create_from_correction(SESSION_A, SPEC)

        active = await service.list_active()

        assert [r.state for r in active] == [RuleState.STAGING]
        assert active[0].id == rule.id


class TestRecordUse:
    async def test_unknown_rule_raises_rule_not_found(self):
        service, _, _ = _service()

        with pytest.raises(RuleNotFoundError):
            await service.record_use(uuid4(), SESSION_A, "book a table", UseOutcome.APPROVAL)

    async def test_record_use_recomputes_and_persists_the_posterior(self):
        """Witness: an implementation that mutates the in-memory rule without
        refresh() leaves the stored confidence at 0.5."""
        service, repository, _ = _service()
        rule = await service.create_from_correction(SESSION_A, SPEC)

        updated = await service.record_use(
            rule.id, SESSION_A, "book a table for two", UseOutcome.APPROVAL
        )

        assert updated.confidence == pytest.approx(ONE_APPROVAL)
        stored = await repository.get(rule.id)
        assert stored is not None
        assert stored.confidence == pytest.approx(ONE_APPROVAL)

    async def test_neutral_use_neither_moves_the_score_nor_promotes(self):
        """Witness: two approvals across two sessions are (2, 2) — in staging.
        Counting the neutral use as a positive makes it (3, 2) and promotes."""
        service, repository, _ = _service()
        rule = await service.create_from_correction(SESSION_A, SPEC)
        await service.record_use(rule.id, SESSION_A, "book", UseOutcome.APPROVAL)
        await service.record_use(rule.id, SESSION_B, "book", UseOutcome.APPROVAL)
        before = await repository.get(rule.id)
        assert before is not None

        updated = await service.record_use(rule.id, SESSION_C, "book", UseOutcome.NEUTRAL)

        assert updated.confidence == pytest.approx(before.confidence)
        assert updated.state is RuleState.STAGING

    async def test_three_positives_across_two_sessions_promote(self):
        """Witness: the same three approvals in one session read (3, 1) and stay
        staging — the session counter has to be real."""
        service, _, _ = _service()
        one_session = await service.create_from_correction(SESSION_A, SPEC)
        for _ in range(3):
            await service.record_use(
                one_session.id, SESSION_A, "book", UseOutcome.APPROVAL
            )
        two_sessions = await service.create_from_correction(SESSION_A, SPEC)
        for session_id in (SESSION_A, SESSION_A, SESSION_B):
            await service.record_use(
                two_sessions.id, session_id, "book", UseOutcome.APPROVAL
            )

        active = {rule.id: rule for rule in await service.list_active()}

        assert active[one_session.id].state is RuleState.STAGING
        assert active[two_sessions.id].state is RuleState.DEFINITIVE
        assert active[two_sessions.id].confidence == pytest.approx(THREE_APPROVALS)

    async def test_two_corrections_archive_a_dictated_rule(self):
        """Witness: one correction only brings 20/21 back to 0.5 (demoted, not
        archived); the second takes it to 1/21 < 0.3."""
        service, repository, clock = _service()
        rule = await service.create_dictated(SESSION_A, SPEC)

        after_one = await service.record_use(
            rule.id, SESSION_B, "book", UseOutcome.CORRECTION
        )
        assert after_one.state is RuleState.STAGING
        assert after_one.confidence == pytest.approx(0.5)

        after_two = await service.record_use(
            rule.id, SESSION_C, "book", UseOutcome.CORRECTION
        )

        assert after_two.state is RuleState.ARCHIVED
        assert after_two.confidence == pytest.approx(1 / 21)
        assert after_two.archived_at == clock.now
        stored = await repository.get(rule.id)
        assert stored is not None
        assert stored.state is RuleState.ARCHIVED

    async def test_archived_rules_never_come_back(self):
        """Witness: 3 approvals across 2 sessions would promote a staging rule;
        an archived one must stay archived."""
        service, _, _ = _service()
        rule = await service.create_dictated(SESSION_A, SPEC)
        await service.record_use(rule.id, SESSION_B, "book", UseOutcome.CORRECTION)
        await service.record_use(rule.id, SESSION_C, "book", UseOutcome.CORRECTION)

        for session_id in (SESSION_A, SESSION_A, SESSION_B):
            updated = await service.record_use(
                rule.id, session_id, "book", UseOutcome.APPROVAL
            )

        assert updated.state is RuleState.ARCHIVED
        assert await service.list_active() == []


class TestListActive:
    async def test_applies_expiry_on_load_and_persists_the_demotion(self):
        """Witness: the stored 20/21 is only corrected because list_active
        recomputes; a sweep that trusts the column returns definitive."""
        service, repository, clock = _service()
        rule = await service.create_dictated(SESSION_A, SPEC)
        clock.advance(VALIDITY_WINDOW + timedelta(days=1))

        active = await service.list_active()

        assert [r.state for r in active] == [RuleState.STAGING]
        stored = await repository.get(rule.id)
        assert stored is not None
        assert stored.state is RuleState.STAGING
        assert stored.confidence == pytest.approx(0.5)

    async def test_promotes_a_staging_rule_whose_evidence_accumulated(self):
        """Witness: the rule is written straight into the repository in staging
        with (3, 2) evidence, so only the sweep can promote it."""
        service, repository, _ = _service()
        rule = Rule(condition="x", action="y", state=RuleState.STAGING, confidence=0.5)
        await repository.create(rule)
        for session_id in (SESSION_A, SESSION_A, SESSION_B):
            await repository.add_use(
                RuleUse(
                    rule_id=rule.id,
                    session_id=session_id,
                    situation="x applies",
                    outcome=UseOutcome.APPROVAL,
                    occurred_at=NOW,
                )
            )

        active = await service.list_active()

        assert [r.state for r in active] == [RuleState.DEFINITIVE]
        stored = await repository.get(rule.id)
        assert stored is not None
        assert stored.state is RuleState.DEFINITIVE
        assert stored.confidence == pytest.approx(THREE_APPROVALS)

    async def test_archived_rules_are_never_returned(self):
        """Witness: an archived rule whose uses would score 0.95 must not enter
        the context."""
        service, _, _ = _service()
        rule = await service.create_dictated(SESSION_A, SPEC)
        await service.archive(rule.id)

        assert await service.list_active() == []

    async def test_list_active_is_capped_at_the_context_budget(self):
        """Witness: no cap returns all 45; the budget is 40."""
        service, repository, _ = _service()
        for index in range(CONTEXT_BUDGET + 5):
            await repository.create(
                Rule(
                    condition=f"condition {index}",
                    action="act",
                    state=RuleState.DEFINITIVE,
                    confidence=0.5 + index / 1000,
                )
            )

        active = await service.list_active()

        assert len(active) == CONTEXT_BUDGET
        # Highest confidence first: the cap keeps the top of the ranking.
        assert active[0].condition == f"condition {CONTEXT_BUDGET + 4}"

    async def test_get_returns_archived_rules_for_the_cli(self):
        """`get` returns any rule — archived included — so the CLI can show it."""
        service, _, _ = _service()
        rule = await service.create_from_correction(SESSION_A, SPEC)
        archived = await service.archive(rule.id)

        assert archived is not None
        fetched = await service.get(rule.id)
        assert fetched is not None
        assert fetched.state is RuleState.ARCHIVED

    async def test_get_unknown_rule_is_none(self):
        service, _, _ = _service()

        assert await service.get(uuid4()) is None


class TestArchive:
    async def test_archive_sets_state_and_timestamp(self):
        service, repository, clock = _service()
        rule = await service.create_dictated(SESSION_A, SPEC)

        archived = await service.archive(rule.id)

        assert archived is not None
        assert archived.state is RuleState.ARCHIVED
        assert archived.archived_at == clock.now
        stored = await repository.get(rule.id)
        assert stored is not None
        assert stored.archived_at == clock.now

    async def test_archive_unknown_rule_is_none(self):
        service, _, _ = _service()

        assert await service.archive(uuid4()) is None


class TestSplit:
    async def _parent_with_four_uses(
        self,
    ) -> tuple[RuleService, InMemoryRuleRepository, FakeClock, Rule, list[RuleUse]]:
        service, repository, clock = _service()
        # A definitive rule written straight into the repository: the dictation
        # path would add its own approval use, and this fixture counts exactly
        # the four uses the assignments have to redistribute.
        parent = await repository.create(
            Rule(
                condition=SPEC.condition,
                action=SPEC.action,
                state=RuleState.DEFINITIVE,
                confidence=ONE_APPROVAL,
                origin=RuleOrigin.DICTATED,
            )
        )
        # Two uses that fall inside child 0's condition, one approval plus one
        # neutral inside child 1's.
        situations = [
            ("dinner for two", UseOutcome.APPROVAL),
            ("dinner with friends", UseOutcome.APPROVAL),
            ("a pizza slice", UseOutcome.APPROVAL),
            ("takeaway pizza", UseOutcome.NEUTRAL),
        ]
        for situation, outcome in situations:
            await service.record_use(parent.id, SESSION_B, situation, outcome)
        uses = await repository.list_uses(parent.id)
        assert len(uses) == 4
        return service, repository, clock, parent, uses

    async def test_split_redistributes_each_use_to_exactly_one_child(self):
        """Witness: copying the parent's uses to both children gives both
        8000/8001; filtering gives 400/401 to child 0 and 20/21 to child 1."""
        service, repository, _, parent, uses = await self._parent_with_four_uses()
        children = [
            RuleSpec(condition="a dinner booking", action="confirm first"),
            RuleSpec(condition="a pizza order", action="order directly"),
        ]
        assignments = {uses[0].id: 0, uses[1].id: 0, uses[2].id: 1, uses[3].id: 1}

        created = await service.split(parent.id, children, assignments)

        assert len(created) == 2
        assert created[0].state is RuleState.DEFINITIVE  # inherits the parent's state
        assert created[1].state is RuleState.STAGING
        assert created[0].confidence == pytest.approx(TWO_APPROVALS)
        assert created[1].confidence == pytest.approx(ONE_APPROVAL)
        stored_situations = {
            use.situation: use.rule_id for use in await repository.list_uses(created[0].id)
        }
        assert stored_situations == {
            "dinner for two": created[0].id,
            "dinner with friends": created[0].id,
        }
        assert {
            use.situation: use.rule_id for use in await repository.list_uses(created[1].id)
        } == {"a pizza slice": created[1].id, "takeaway pizza": created[1].id}

    async def test_split_archives_the_parent_superseded_by_the_first_child(self):
        service, repository, clock, parent, uses = await self._parent_with_four_uses()
        assignments = {uses[0].id: 0, uses[1].id: 0, uses[2].id: 1, uses[3].id: 1}

        created = await service.split(
            parent.id,
            [
                RuleSpec(condition="dinner", action="confirm first"),
                RuleSpec(condition="pizza", action="order directly"),
            ],
            assignments,
        )

        stored = await repository.get(parent.id)
        assert stored is not None
        assert stored.state is RuleState.ARCHIVED
        assert stored.superseded_by == created[0].id
        assert stored.archived_at == clock.now
        # The parent no longer scores: none of the uses are still attached to it.
        assert await repository.list_uses(parent.id) == []

    async def test_split_needs_at_least_two_children(self):
        """Witness: an implementation that creates the children first leaves a
        write behind when it then raises."""
        service, repository, _, parent, uses = await self._parent_with_four_uses()

        with pytest.raises(ValueError):
            await service.split(
                parent.id,
                [RuleSpec(condition="dinner", action="confirm first")],
                {uses[0].id: 0},
            )

        assert set(repository.rules) == {parent.id}

    async def test_split_unknown_parent_raises_rule_not_found(self):
        service, _, _ = _service()

        with pytest.raises(RuleNotFoundError):
            await service.split(
                uuid4(),
                [
                    RuleSpec(condition="a", action="b"),
                    RuleSpec(condition="c", action="d"),
                ],
                {},
            )

    async def test_split_rejects_an_assignment_out_of_range(self):
        service, repository, _, parent, uses = await self._parent_with_four_uses()

        with pytest.raises(ValueError):
            await service.split(
                parent.id,
                [
                    RuleSpec(condition="dinner", action="confirm first"),
                    RuleSpec(condition="pizza", action="order directly"),
                ],
                {uses[0].id: 0, uses[1].id: 1, uses[2].id: 2, uses[3].id: 1},
            )

        assert set(repository.rules) == {parent.id}

    async def test_split_rejects_an_unassigned_use(self):
        """Witness: silently leaving a use on the archived parent loses evidence;
        every use must land with exactly one child."""
        service, repository, _, parent, uses = await self._parent_with_four_uses()

        with pytest.raises(ValueError):
            await service.split(
                parent.id,
                [
                    RuleSpec(condition="dinner", action="confirm first"),
                    RuleSpec(condition="pizza", action="order directly"),
                ],
                {uses[0].id: 0, uses[1].id: 0, uses[2].id: 1},
            )

        assert set(repository.rules) == {parent.id}


class TestMerge:
    async def test_merge_takes_the_union_of_the_parents_uses(self):
        """Witness: keeping only the first parent's uses gives 400/401; the union
        of (2 approvals) and (1 approval) gives 8000/8001."""
        service, repository, _ = _service()
        first = await service.create_from_correction(SESSION_A, RuleSpec("a", "b"))
        second = await service.create_from_correction(SESSION_B, RuleSpec("c", "d"))
        for _ in range(2):
            await service.record_use(first.id, SESSION_A, "a applies", UseOutcome.APPROVAL)
        await service.record_use(second.id, SESSION_B, "c applies", UseOutcome.APPROVAL)

        fused = await service.merge([first.id, second.id], RuleSpec("a or c", "b or d"))

        assert fused.origin is RuleOrigin.MERGE
        assert fused.confidence == pytest.approx(THREE_APPROVALS)
        assert len(await repository.list_uses(fused.id)) == 3

    async def test_merge_is_definitive_only_if_every_parent_was(self):
        """Witness: "any parent definitive" returns definitive for the mixed
        pair; "all parents" returns staging."""
        service, _, _ = _service()
        definitive = await service.create_dictated(SESSION_A, RuleSpec("a", "b"))
        staging = await service.create_from_correction(SESSION_B, RuleSpec("c", "d"))

        mixed = await service.merge(
            [definitive.id, staging.id], RuleSpec("a or c", "b or d")
        )

        assert mixed.state is RuleState.STAGING

    async def test_merge_of_two_definitive_parents_is_definitive(self):
        service, _, _ = _service()
        first = await service.create_dictated(SESSION_A, RuleSpec("a", "b"))
        second = await service.create_dictated(SESSION_B, RuleSpec("c", "d"))

        fused = await service.merge([first.id, second.id], RuleSpec("a or c", "b or d"))

        assert fused.state is RuleState.DEFINITIVE
        assert fused.confidence == pytest.approx(TWO_APPROVALS)

    async def test_merge_archives_the_parents_superseded_by_the_fused_rule(self):
        service, repository, clock = _service()
        first = await service.create_dictated(SESSION_A, RuleSpec("a", "b"))
        second = await service.create_dictated(SESSION_B, RuleSpec("c", "d"))

        fused = await service.merge([first.id, second.id], RuleSpec("a or c", "b or d"))

        for parent_id in (first.id, second.id):
            stored = await repository.get(parent_id)
            assert stored is not None
            assert stored.state is RuleState.ARCHIVED
            assert stored.superseded_by == fused.id
            assert stored.archived_at == clock.now
            assert await repository.list_uses(parent_id) == []

    async def test_merge_needs_at_least_two_parents(self):
        service, repository, _ = _service()
        rule = await service.create_from_correction(SESSION_A, RuleSpec("a", "b"))

        with pytest.raises(ValueError):
            await service.merge([rule.id], RuleSpec("a", "b"))

        assert set(repository.rules) == {rule.id}

    async def test_merge_unknown_parent_raises_rule_not_found(self):
        """Witness: reading the parents before writing means a failed merge
        leaves no fused row behind."""
        service, repository, _ = _service()
        rule = await service.create_from_correction(SESSION_A, RuleSpec("a", "b"))

        with pytest.raises(RuleNotFoundError):
            await service.merge([rule.id, uuid4()], RuleSpec("a", "b"))

        assert set(repository.rules) == {rule.id}


class TestRenderRules:
    def test_no_rules_renders_nothing(self):
        assert render_rules([]) is None

    def test_staged_rule_is_marked_provisional_and_asks_for_a_declaration(self):
        """Witness: a definitive-only renderer marks every rule provisional, and
        a renderer that never marks one hides the staged rule's status."""
        staged = Rule(
            condition="the user asks to book a table",
            action="ask for confirmation before booking",
            rationale="she was charged for a restaurant she did not choose",
            state=RuleState.STAGING,
        )

        text = render_rules([staged])

        assert text is not None
        assert "the user asks to book a table" in text
        assert "ask for confirmation before booking" in text
        assert "she was charged for a restaurant she did not choose" in text
        assert "provisional" in text.lower()
        assert "one line" in text.lower()

    def test_definitive_rule_is_not_marked_provisional(self):
        """Witness: the same rule in `definitive` must lose the marker — the
        difference between the two states is the whole point of the marker."""
        staged = Rule(condition="book a table", action="confirm first", state=RuleState.STAGING)
        definitive = Rule(
            condition="book a table", action="confirm first", state=RuleState.DEFINITIVE
        )

        staged_text = render_rules([staged])
        definitive_text = render_rules([definitive])

        assert staged_text is not None
        assert definitive_text is not None
        assert "provisional" in staged_text.lower()
        assert "provisional" not in definitive_text.lower()
        assert "book a table" in definitive_text
        assert "confirm first" in definitive_text

    def test_archived_rules_are_never_rendered(self):
        """Witness: archived is not applied; a renderer that passes every rule
        through puts the retired one in the prompt."""
        archived = Rule(condition="retired rule", action="do not do this", state=RuleState.ARCHIVED)
        definitive = Rule(condition="live rule", action="do this", state=RuleState.DEFINITIVE)

        assert render_rules([archived]) is None
        text = render_rules([definitive, archived])
        assert text is not None
        assert "retired rule" not in text
        assert "live rule" in text


class TestDeclaresItself:
    def test_only_a_staged_rule_declares_itself(self):
        assert Rule(state=RuleState.STAGING).declares_itself() is True
        assert Rule(state=RuleState.DEFINITIVE).declares_itself() is False
        assert Rule(state=RuleState.ARCHIVED).declares_itself() is False
