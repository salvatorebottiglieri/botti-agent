"""Cortex Rules Module.

The learned-rule system (issue #130, slice A): a Rule is a prescription
applied when a condition holds, scored by the outcomes of its uses with the
same logit-space odds update ADR-0013 uses for Facts. Mechanism:
``docs/specs/rule-system.md``; decision: ``docs/adr/0020-learned-rules.md``.
"""

from cortex.rules.constants import CONTEXT_BUDGET, PRIOR
from cortex.rules.interfaces import RuleRepository
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
from cortex.rules.repository import PostgresRuleRepository
from cortex.rules.scoring import active_uses, next_state, posterior, use_counts
from cortex.rules.service import RuleService

__all__ = [
    # Constants
    "CONTEXT_BUDGET",
    "PRIOR",
    # Models
    "Rule",
    "RuleOrigin",
    "RuleSpec",
    "RuleState",
    "RuleUse",
    "UseOutcome",
    "RuleNotFoundError",
    # Score
    "active_uses",
    "next_state",
    "posterior",
    "use_counts",
    # Interfaces
    "RuleRepository",
    # Implementations
    "PostgresRuleRepository",
    "RuleService",
    # Rendering
    "render_rules",
]
