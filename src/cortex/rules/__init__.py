"""Cortex Rules Module.

The learned-rule system (issue #130, slice A): a Rule is a prescription
applied when a condition holds, scored by the outcomes of its uses with the
same logit-space odds update ADR-0013 uses for Facts. Mechanism:
``docs/specs/rule-system.md``; decision: ``docs/adr/0020-learned-rules.md``.

``interfaces`` and ``models`` stay open; every other submodule is private.
"""

from cortex.rules import render
from cortex.rules.queue import enqueue_turn
from cortex.rules.service import RuleService

__all__ = ["RuleService", "render", "enqueue_turn"]
