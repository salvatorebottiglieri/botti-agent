"""Score invariants for the learned-rule system (docs/specs/rule-system.md).

Each test names its WITNESS: a concrete input on which the correct
implementation and the plausible wrong one diverge. A fixed point of the
transformation (a rule with a single approval use when testing promotion) is
not a witness — the numbers below are chosen so 2-vs-3 positives and 1-vs-2
sessions actually matter.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from cortex.rules.constants import (
    ARCHIVE_THRESHOLD,
    CONTEXT_BUDGET,
    DEMOTE_THRESHOLD,
    LR,
    MIN_DISTINCT_SESSIONS,
    MIN_POSITIVE_USES,
    PRIOR,
    PROMOTE_THRESHOLD,
    VALIDITY_WINDOW,
)
from cortex.rules.models import RuleState, RuleUse, UseOutcome
from cortex.rules.scoring import active_uses, next_state, posterior, use_counts

NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)

SESSION_A = UUID("11111111-1111-4111-8111-111111111111")
SESSION_B = UUID("22222222-2222-4222-8222-222222222222")
SESSION_C = UUID("33333333-3333-4333-8333-333333333333")

# sigmoid(ln 20) = 20/21 — one approval on top of the 0.5 prior.
ONE_APPROVAL = 20 / 21
# sigmoid(2 ln 20) = 400/401 — two approvals.
TWO_APPROVALS = 400 / 401


def _use(
    outcome: UseOutcome,
    *,
    session_id: UUID = SESSION_A,
    occurred_at: datetime = NOW,
) -> RuleUse:
    """One use of a rule, active by default (occurred_at == NOW)."""
    return RuleUse(
        rule_id=uuid4(),
        session_id=session_id,
        situation="book a table",
        outcome=outcome,
        occurred_at=occurred_at,
    )


def _approvals(count: int, session_id: UUID = SESSION_A) -> list[RuleUse]:
    return [_use(UseOutcome.APPROVAL, session_id=session_id) for _ in range(count)]


class TestConstants:
    """The calibration table is the spec's, not a taste."""

    def test_constants_match_the_spec_table(self):
        """Witness: any drifted constant changes every posterior below."""
        assert PRIOR == 0.5
        assert LR[UseOutcome.APPROVAL] == 20
        assert LR[UseOutcome.CORRECTION] == 20
        assert LR[UseOutcome.IMPLICIT] == 3
        assert LR[UseOutcome.NEUTRAL] == 1
        assert LR[UseOutcome.SCOPE_CORRECTION] == 1
        assert PROMOTE_THRESHOLD == 0.9
        assert DEMOTE_THRESHOLD == 0.7
        assert ARCHIVE_THRESHOLD == 0.3
        assert MIN_POSITIVE_USES == 3
        assert MIN_DISTINCT_SESSIONS == 2
        assert CONTEXT_BUDGET == 40
        # Six months, expressed in days: 183 = 6 x the mean month length.
        assert VALIDITY_WINDOW == timedelta(days=183)


class TestPosterior:
    """logit(p') = logit(p) + sum(direction · ln LR), recomputed over active uses."""

    def test_one_approval_moves_the_prior_up_by_ln_20(self):
        """Witness: sign ignored — a correction read as +1 also lands at 0.95."""
        assert posterior(PRIOR, _approvals(1), NOW) == pytest.approx(ONE_APPROVAL)

    def test_one_correction_moves_the_prior_down_by_ln_20(self):
        """Witness: an implementation that forgets the -1 direction returns 0.95."""
        result = posterior(PRIOR, [_use(UseOutcome.CORRECTION)], NOW)
        assert result == pytest.approx(1 - ONE_APPROVAL)

    def test_implicit_acceptance_is_the_weak_positive(self):
        """Witness: implicit read as strong (LR 20) gives 0.95, not sigmoid(ln 3)."""
        result = posterior(PRIOR, [_use(UseOutcome.IMPLICIT)], NOW)
        assert result == pytest.approx(0.75)
        assert result < ONE_APPROVAL

    def test_empty_use_list_returns_the_prior(self):
        assert posterior(PRIOR, [], NOW) == PRIOR

    def test_posterior_stays_inside_the_unit_interval_under_extreme_evidence(self):
        """Witness: a linear `prior + n·step` update leaves [0, 1] after 50 uses."""
        many_approvals = posterior(PRIOR, _approvals(50), NOW)
        many_corrections = posterior(
            PRIOR, [_use(UseOutcome.CORRECTION) for _ in range(50)], NOW
        )

        assert 0.0 <= many_approvals <= 1.0
        assert 0.0 <= many_corrections <= 1.0
        assert many_approvals > 0.999
        assert many_corrections < 0.001

    def test_neutral_use_moves_nothing(self):
        """Witness: silence read as approval. Two approvals score 400/401;
        adding a third positive (or a strong +1 on an LR-1 outcome) would give
        8000/8001, so `neutral == approval` cannot pass this test."""
        two = posterior(PRIOR, _approvals(2), NOW)
        two_plus_silence = posterior(PRIOR, _approvals(2) + [_use(UseOutcome.NEUTRAL)], NOW)
        three = posterior(PRIOR, _approvals(3), NOW)

        assert two == pytest.approx(TWO_APPROVALS)
        assert two_plus_silence == two
        assert two_plus_silence != pytest.approx(three)

    def test_scope_correction_does_not_penalise_the_parent(self):
        """Witness: a scope correction scored as a correction (-ln 20) drops
        0.9526 back to 0.5 instead of leaving it untouched."""
        one = posterior(PRIOR, _approvals(1), NOW)
        with_scope_correction = posterior(
            PRIOR, _approvals(1) + [_use(UseOutcome.SCOPE_CORRECTION)], NOW
        )

        assert with_scope_correction == pytest.approx(one)

    def test_prior_outside_the_open_unit_interval_raises(self):
        for bad_prior in (0.0, 1.0, -0.1, 1.1):
            with pytest.raises(ValueError):
                posterior(bad_prior, [], NOW)


class TestValidityWindow:
    """Uses older than the window stop contributing; they are not deleted."""

    def test_active_uses_keeps_the_inside_boundary_and_drops_the_outside_one(self):
        """Witness: the pair straddles the window by one second, so an
        implementation with no window, or with a window of the wrong length
        (6 weeks, 6 days), separates them where this test does not."""
        just_inside = _use(
            UseOutcome.APPROVAL, occurred_at=NOW - VALIDITY_WINDOW + timedelta(seconds=1)
        )
        just_outside = _use(
            UseOutcome.APPROVAL, occurred_at=NOW - VALIDITY_WINDOW - timedelta(seconds=1)
        )

        kept = active_uses([just_inside, just_outside], NOW)

        assert kept == [just_inside]

    def test_expired_use_contributes_nothing(self):
        """Witness: a 6-month-old dictation must expire (posterior back to the
        0.5 prior); a use one second inside the window must not."""
        expired = _use(
            UseOutcome.APPROVAL, occurred_at=NOW - VALIDITY_WINDOW - timedelta(seconds=1)
        )
        fresh = _use(
            UseOutcome.APPROVAL, occurred_at=NOW - VALIDITY_WINDOW + timedelta(seconds=1)
        )

        assert posterior(PRIOR, [expired], NOW) == pytest.approx(PRIOR)
        assert posterior(PRIOR, [fresh], NOW) == pytest.approx(ONE_APPROVAL)


class TestUseCounts:
    """Promotion counters count active positive uses only."""

    def test_counts_positives_and_their_distinct_sessions(self):
        uses = [
            _use(UseOutcome.APPROVAL, session_id=SESSION_A),
            _use(UseOutcome.IMPLICIT, session_id=SESSION_A),
            _use(UseOutcome.APPROVAL, session_id=SESSION_B),
            _use(UseOutcome.NEUTRAL, session_id=SESSION_C),
        ]

        assert use_counts(uses, NOW) == (3, 2)

    def test_neutral_uses_are_not_positive_uses(self):
        """Witness: silence counted as a positive gives (3, 2) here, which is
        the promotion bar; the honest count is (2, 2) and stays in staging."""
        uses = [
            _use(UseOutcome.APPROVAL, session_id=SESSION_A),
            _use(UseOutcome.APPROVAL, session_id=SESSION_B),
            _use(UseOutcome.NEUTRAL, session_id=SESSION_C),
        ]

        assert use_counts(uses, NOW) == (2, 2)

    def test_expired_positives_are_not_counted(self):
        """Witness: 3 approvals with one expired reads (2, 2) — below the bar —
        while a no-window implementation reads (3, 2) and promotes."""
        old = _use(
            UseOutcome.APPROVAL,
            session_id=SESSION_C,
            occurred_at=NOW - VALIDITY_WINDOW - timedelta(days=1),
        )
        uses = [
            _use(UseOutcome.APPROVAL, session_id=SESSION_A),
            _use(UseOutcome.APPROVAL, session_id=SESSION_B),
            old,
        ]

        assert use_counts(uses, NOW) == (2, 2)


class TestNextState:
    """The lifecycle decision: promote, demote, archive, otherwise unchanged."""

    def test_promotion_needs_three_positives_across_two_sessions(self):
        """Witness: three approvals in ONE session read (3, 1) — an
        implementation that ignores the session count promotes."""
        confidence = posterior(PRIOR, _approvals(3), NOW)

        assert next_state(RuleState.STAGING, confidence, _approvals(3), NOW) is RuleState.STAGING

    def test_promotion_needs_more_than_two_positives(self):
        """Witness: two approvals across two sessions are (2, 2) and score
        0.9975 — an implementation that promotes on confidence alone, or on
        two positives, returns definitive."""
        two_sessions = [
            _use(UseOutcome.APPROVAL, session_id=SESSION_A),
            _use(UseOutcome.APPROVAL, session_id=SESSION_B),
        ]
        confidence = posterior(PRIOR, two_sessions, NOW)

        assert next_state(RuleState.STAGING, confidence, two_sessions, NOW) is RuleState.STAGING

    def test_three_positives_across_two_sessions_promote(self):
        """Witness: the same evidence as above plus one more positive crosses
        both counters, so a missing counter fails one of the two tests."""
        uses = [
            _use(UseOutcome.APPROVAL, session_id=SESSION_A),
            _use(UseOutcome.APPROVAL, session_id=SESSION_A),
            _use(UseOutcome.APPROVAL, session_id=SESSION_B),
        ]
        confidence = posterior(PRIOR, uses, NOW)

        assert confidence >= PROMOTE_THRESHOLD
        assert next_state(RuleState.STAGING, confidence, uses, NOW) is RuleState.DEFINITIVE

    def test_correction_born_rule_stays_in_staging(self):
        """Witness: a fresh rule (0.5, no uses) must not be archived (0.5 > 0.3)
        and must not be promoted — the demotion threshold does not touch it."""
        assert next_state(RuleState.STAGING, PRIOR, [], NOW) is RuleState.STAGING

    def test_dictated_rule_does_not_demote(self):
        """Witness: with a bare 0.5 prior (no dictation use) the dictation-born
        rule would sit at 0.5 < 0.7 and demote on the first sweep."""
        confidence = posterior(PRIOR, _approvals(1), NOW)

        assert confidence >= PROMOTE_THRESHOLD
        assert next_state(RuleState.DEFINITIVE, confidence, _approvals(1), NOW) is (
            RuleState.DEFINITIVE
        )

    def test_definitive_demotes_below_seven_tenths(self):
        """Witness: 0.69 demotes, 0.70 does not — a `<=` comparison demotes the
        boundary case too."""
        assert next_state(RuleState.DEFINITIVE, 0.69, [], NOW) is RuleState.STAGING
        assert next_state(RuleState.DEFINITIVE, DEMOTE_THRESHOLD, [], NOW) is (
            RuleState.DEFINITIVE
        )

    def test_staging_archives_below_three_tenths(self):
        """Witness: 0.29 archives, 0.30 does not — 0.3 must not be archived."""
        assert next_state(RuleState.STAGING, 0.29, [], NOW) is RuleState.ARCHIVED
        assert next_state(RuleState.STAGING, ARCHIVE_THRESHOLD, [], NOW) is RuleState.STAGING

    def test_archived_stays_archived(self):
        """Witness: perfect evidence (3 positives, 2 sessions, 0.999) must not
        resurrect an archived rule."""
        uses = [
            _use(UseOutcome.APPROVAL, session_id=SESSION_A),
            _use(UseOutcome.APPROVAL, session_id=SESSION_A),
            _use(UseOutcome.APPROVAL, session_id=SESSION_B),
        ]

        assert next_state(RuleState.ARCHIVED, 0.999, uses, NOW) is RuleState.ARCHIVED

    def test_definitive_with_confident_evidence_stays_definitive(self):
        assert next_state(RuleState.DEFINITIVE, 0.95, _approvals(1), NOW) is (
            RuleState.DEFINITIVE
        )
