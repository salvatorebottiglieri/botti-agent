"""promotion_line — the one sentence the user reads when a rule is promoted.

Fixed copy in the ``condition → action`` shape ``render_rules`` gives the
model: no provider call, so the announcement cannot hallucinate and costs
nothing. No rationale, no id and no score — those are the model's evidence,
not the user's notification.
"""

from __future__ import annotations

from cortex.rules.models import Rule


def promotion_line(rule: Rule) -> str:
    """The announcement of one promotion, in the shape the rules render in."""
    return f"Rule confirmed: {rule.condition} → {rule.action}"
