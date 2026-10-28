"""The rule system's calibration constants — the only magic numbers in it.

Every value is copied from the table in ``docs/specs/rule-system.md``
(## Calibration constants) and is deliberately isolated here: the score, the
lifecycle and the context budget all read them from this one module.
"""

from datetime import timedelta

from cortex.rules.models import UseOutcome

#: Prior for a rule born from a correction: no evidence yet.
PRIOR = 0.5

#: Strength of the signal per outcome. A dictation is written as one
#: `approval` use, so a dictated rule starts at sigmoid(ln 20) ~= 0.952.
LR: dict[UseOutcome, float] = {
    UseOutcome.APPROVAL: 20.0,
    UseOutcome.CORRECTION: 20.0,
    UseOutcome.IMPLICIT: 3.0,
    UseOutcome.NEUTRAL: 1.0,
    UseOutcome.SCOPE_CORRECTION: 1.0,
}

#: Direction of each outcome in logit space. A scope correction triggers a
#: Split; it does not penalise the parent.
DIRECTION: dict[UseOutcome, int] = {
    UseOutcome.APPROVAL: 1,
    UseOutcome.CORRECTION: -1,
    UseOutcome.IMPLICIT: 1,
    UseOutcome.SCOPE_CORRECTION: 0,
    UseOutcome.NEUTRAL: 0,
}

#: Uses older than this stop contributing; they are never deleted. Six months
#: in days (183 = 6 x the mean month length) so the window needs no calendar
#: library.
VALIDITY_WINDOW = timedelta(days=183)

PROMOTE_THRESHOLD = 0.9
DEMOTE_THRESHOLD = 0.7
ARCHIVE_THRESHOLD = 0.3
MIN_POSITIVE_USES = 3
MIN_DISTINCT_SESSIONS = 2

#: How many rules may enter the context bundle.
CONTEXT_BUDGET = 40
