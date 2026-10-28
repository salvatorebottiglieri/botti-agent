"""render_rules — the rule set as one system-prompt section.

Staged rules are marked provisional and the text instructs the model to declare
the rule in one line when it applies it, so the user can confirm or correct it.
Definitive rules are silent: only the use attribution records them.
"""

from __future__ import annotations

from collections.abc import Sequence

from cortex.rules.models import Rule, RuleState

_INTRO = "Rules learned with the user (apply a rule when its condition holds):"
_DECLARATION_INSTRUCTION = (
    "Provisional rules are not confirmed yet: when you apply one, state in one "
    "line which rule you are applying."
)


def render_rules(rules: Sequence[Rule]) -> str | None:
    """The rules section, or None when there is nothing to say.

    Archived rules are dropped: they are queryable but never applied.
    """
    applicable = [rule for rule in rules if rule.state is not RuleState.ARCHIVED]
    if not applicable:
        return None

    lines = [_INTRO]
    for rule in applicable:
        line = f"- {rule.condition} → {rule.action}"
        if rule.rationale:
            line += f" (because {rule.rationale})"
        if rule.declares_itself():
            line += " [provisional]"
        lines.append(line)

    if any(rule.declares_itself() for rule in applicable):
        lines.append(_DECLARATION_INSTRUCTION)
    return "\n".join(lines)
