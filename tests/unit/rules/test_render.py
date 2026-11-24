"""promotion_line — the one sentence the user reads when a rule is promoted.

Fixed copy, no provider call: only the condition and the action may reach the
user — never the rationale, the id or the score.
"""

from cortex.rules.models import Rule
from cortex.rules.render import promotion_line


def test_the_line_is_the_condition_and_the_action_in_the_render_shape():
    rule = Rule(
        condition="the user asks to book a table",
        action="ask for confirmation before booking",
    )

    assert (
        promotion_line(rule)
        == "Rule confirmed: the user asks to book a table → "
        "ask for confirmation before booking"
    )


def test_the_line_carries_no_rationale_no_id_and_no_confidence():
    """Witness: appending the rationale or the score, as `render_rules` does
    for the model, would put hidden evidence and a number in front of the
    user."""
    rule = Rule(
        condition="c",
        action="a",
        rationale="she was charged for a restaurant she did not choose",
        confidence=0.952,
    )

    assert promotion_line(rule) == "Rule confirmed: c → a"
