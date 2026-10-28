"""The rule score: a pure logit-space odds update over a rule's uses.

    logit(p') = logit(prior) + sum(direction(use) · ln(LR(outcome)))

Only *active* uses contribute — those inside the validity window — which is
what makes expiry work: a use that ages out stops contributing and the
posterior returns toward the prior on its own. The stored confidence is always
this recomputed posterior, never an accumulated counter.

Constants live in ``cortex.rules.constants``; this module holds no numbers.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime

from cortex.rules.constants import (
    ARCHIVE_THRESHOLD,
    DEMOTE_THRESHOLD,
    DIRECTION,
    LR,
    MIN_DISTINCT_SESSIONS,
    MIN_POSITIVE_USES,
    PROMOTE_THRESHOLD,
    VALIDITY_WINDOW,
)
from cortex.rules.models import RuleState, RuleUse


def active_uses(uses: Sequence[RuleUse], now: datetime) -> list[RuleUse]:
    """The uses that still count: `occurred_at` at or after `now - window`."""
    cutoff = now - VALIDITY_WINDOW
    return [use for use in uses if use.occurred_at >= cutoff]


def use_counts(uses: Sequence[RuleUse], now: datetime) -> tuple[int, int]:
    """`(positive uses, distinct sessions)` over the active uses.

    Positive means a `+1` outcome (`approval` or `implicit`); silence
    (`neutral`) and a scope correction are never positives. The session count
    is the number of distinct sessions those positives came from — the two
    promotion counters of the spec.
    """
    positives = [use for use in active_uses(uses, now) if DIRECTION[use.outcome] > 0]
    return len(positives), len({use.session_id for use in positives})


def posterior(prior: float, uses: Sequence[RuleUse], now: datetime) -> float:
    """Recomputed confidence: the sigmoid of the prior's logit plus every
    active use's contribution. An empty active set returns the prior."""
    if not 0.0 < prior < 1.0:
        raise ValueError(f"prior must be in (0, 1), got {prior}")

    logit = math.log(prior / (1.0 - prior))
    for use in active_uses(uses, now):
        logit += DIRECTION[use.outcome] * math.log(LR[use.outcome])
    return _sigmoid(logit)


def next_state(
    current: RuleState,
    confidence: float,
    uses: Sequence[RuleUse],
    now: datetime,
) -> RuleState:
    """The lifecycle decision for one rule at one sweep.

    Archived stays archived; below the archive threshold archives; staging
    promotes on confidence *and* both counters; definitive demotes below the
    demotion threshold; otherwise the state is unchanged.
    """
    if current is RuleState.ARCHIVED:
        return RuleState.ARCHIVED
    if confidence < ARCHIVE_THRESHOLD:
        return RuleState.ARCHIVED

    if current is RuleState.STAGING:
        positives, sessions = use_counts(uses, now)
        if (
            confidence >= PROMOTE_THRESHOLD
            and positives >= MIN_POSITIVE_USES
            and sessions >= MIN_DISTINCT_SESSIONS
        ):
            return RuleState.DEFINITIVE
        return RuleState.STAGING

    if confidence < DEMOTE_THRESHOLD:
        return RuleState.STAGING
    return RuleState.DEFINITIVE


def _sigmoid(logit: float) -> float:
    """1 / (1 + e^-logit), written so a large |logit| cannot overflow."""
    if logit >= 0:
        return 1.0 / (1.0 + math.exp(-logit))
    scaled = math.exp(logit)
    return scaled / (1.0 + scaled)
