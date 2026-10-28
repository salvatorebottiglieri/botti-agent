"""In-memory RuleRepository double for the rule-system tests.

Reads and writes copy the Rule, so a mutation that is not persisted through
``refresh()`` has no effect — the same staleness the Postgres row mapping gives
you. That is what lets a test tell "recomputed and stored" from "mutated in
memory".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from uuid import UUID

from cortex.rules.interfaces import RuleRepository
from cortex.rules.models import Rule, RuleState, RuleUse


class InMemoryRuleRepository(RuleRepository):
    """A RuleRepository over two lists, with the ids and ordering of Postgres."""

    def __init__(self) -> None:
        self.rules: dict[UUID, Rule] = {}
        self.uses: list[RuleUse] = []
        self._next_use_id = 1

    async def create(self, rule: Rule) -> Rule:
        self.rules[rule.id] = replace(rule)
        return replace(rule)

    async def get(self, rule_id: UUID) -> Rule | None:
        rule = self.rules.get(rule_id)
        return replace(rule) if rule is not None else None

    async def add_use(self, use: RuleUse) -> RuleUse:
        stored = replace(use, id=self._next_use_id)
        self._next_use_id += 1
        self.uses.append(stored)
        return replace(stored)

    async def list_uses(self, rule_id: UUID) -> list[RuleUse]:
        matching = [replace(use) for use in self.uses if use.rule_id == rule_id]
        return sorted(matching, key=lambda use: use.occurred_at)

    async def list_by_states(
        self, states: Sequence[RuleState], *, limit: int = 500
    ) -> list[Rule]:
        matching = [replace(rule) for rule in self.rules.values() if rule.state in states]
        matching.sort(key=lambda rule: rule.confidence, reverse=True)
        return matching[:limit]

    async def refresh(self, rule: Rule) -> Rule | None:
        if rule.id not in self.rules:
            return None
        self.rules[rule.id] = replace(rule)
        return replace(rule)

    async def reassign_uses(self, use_ids: Sequence[int], rule_id: UUID) -> int:
        wanted = set(use_ids)
        reassigned = 0
        for use in self.uses:
            if use.id in wanted:
                use.rule_id = rule_id
                reassigned += 1
        return reassigned
