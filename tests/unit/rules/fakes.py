"""In-memory doubles for the rule-system tests.

Reads and writes copy the Rule, so a mutation that is not persisted through
``refresh()`` has no effect — the same staleness the Postgres row mapping gives
you. That is what lets a test tell "recomputed and stored" from "mutated in
memory". ``InMemoryFeedbackQueue`` gives the queue the same ids and ordering.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from cortex.llm.base import LLMClient
from cortex.llm.models import ChatMessage, ChatResult, Role
from cortex.rules.interfaces import FeedbackQueue, RuleRepository
from cortex.rules.models import Rule, RuleState, RuleUse
from cortex.rules.queue import QueuedFeedback, QueuedFeedbackStatus


class DuplicateUseError(RuntimeError):
    """The in-memory copy of the partial unique index on
    ``rule_uses (feedback_id, rule_id)``: a turn scores a rule at most once."""


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
        if use.feedback_id is not None and any(
            stored.feedback_id == use.feedback_id and stored.rule_id == use.rule_id
            for stored in self.uses
        ):
            raise DuplicateUseError(
                f"rule {use.rule_id} already has a use from feedback {use.feedback_id}"
            )
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

    async def list_children(self, rule_id: UUID) -> list[Rule]:
        matching = [
            replace(rule)
            for rule in self.rules.values()
            if rule.superseded_by == rule_id or rule.parent_id == rule_id
        ]
        return sorted(matching, key=lambda rule: rule.created_at)

    async def refresh(self, rule: Rule) -> Rule | None:
        if rule.id not in self.rules:
            return None
        self.rules[rule.id] = replace(rule)
        return replace(rule)

    async def reassign_uses(self, use_ids: Sequence[int], rule_id: UUID) -> None:
        wanted = set(use_ids)
        for use in self.uses:
            if use.id in wanted:
                use.rule_id = rule_id


class InMemoryFeedbackQueue(FeedbackQueue):
    """A FeedbackQueue over a list, with the ids and ordering of Postgres."""

    def __init__(self) -> None:
        self.items: list[QueuedFeedback] = []

    async def enqueue(self, item: QueuedFeedback) -> QueuedFeedback:
        if not item.user_reply.strip():
            raise ValueError("a turn with no user reply is never enqueued")
        stored = replace(item, id=item.id or uuid4())
        self.items.append(stored)
        return replace(stored)

    async def list_pending(self, limit: int = 10) -> list[QueuedFeedback]:
        pending = [
            replace(item)
            for item in self.items
            if item.status is QueuedFeedbackStatus.PENDING
        ]
        pending.sort(key=lambda item: item.occurred_at)
        return pending[:limit]

    async def mark_attributed(self, feedback_id: UUID, now: datetime) -> None:
        for index, item in enumerate(self.items):
            if item.id == feedback_id and item.status is QueuedFeedbackStatus.PENDING:
                self.items[index] = replace(
                    item, status=QueuedFeedbackStatus.ATTRIBUTED, attributed_at=now
                )

    async def drop_stale(self, cutoff: datetime) -> int:
        dropped = 0
        for index, item in enumerate(self.items):
            if item.status is QueuedFeedbackStatus.PENDING and item.occurred_at < cutoff:
                self.items[index] = replace(item, status=QueuedFeedbackStatus.DROPPED)
                dropped += 1
        return dropped


class RaisingQueue(InMemoryFeedbackQueue):
    """A queue whose insert fails the way a dropped connection does."""

    async def enqueue(self, item: QueuedFeedback) -> QueuedFeedback:
        raise RuntimeError("connection lost")


def llm_returning(content: str | None) -> MagicMock:
    """An LLM client stub whose ``chat()`` returns ``content``."""
    llm = MagicMock(spec=LLMClient)
    llm.chat = AsyncMock(
        return_value=ChatResult(message=ChatMessage(role=Role.ASSISTANT, content=content))
    )
    return llm


def sent_text(llm: MagicMock) -> str:
    """Everything the stub was sent, as one string, for prompt assertions."""
    messages: list[ChatMessage] = llm.chat.call_args.args[0]
    return "\n".join(m.content or "" for m in messages)
