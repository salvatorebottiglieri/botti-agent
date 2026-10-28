"""Rule system data models (docs/specs/rule-system.md ## Data model)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID


class RuleState(StrEnum):
    """Where a rule sits in its lifecycle."""

    STAGING = "staging"
    DEFINITIVE = "definitive"
    ARCHIVED = "archived"


class RuleOrigin(StrEnum):
    """How a rule was born. The schema has no `split` member: a Split's
    children are born from the scope correction that triggered them."""

    DICTATED = "dictated"
    CORRECTION = "correction"
    MERGE = "merge"


class UseOutcome(StrEnum):
    """The feedback category of one use — a category, never a number."""

    APPROVAL = "approval"
    IMPLICIT = "implicit"
    CORRECTION = "correction"
    SCOPE_CORRECTION = "scope_correction"
    NEUTRAL = "neutral"


class RuleNotFoundError(LookupError):
    """A rule id that no row matches."""


@dataclass(frozen=True)
class RuleSpec:
    """The text of a rule, as dictated or proposed (no score, no state)."""

    condition: str
    action: str
    rationale: str = ""


@dataclass
class Rule:
    """A learned prescription with its recomputed confidence and state."""

    id: UUID = field(default_factory=uuid.uuid4)
    condition: str = ""
    action: str = ""
    rationale: str = ""
    state: RuleState = RuleState.STAGING
    # Mirrors constants.PRIOR; the literal is repeated because importing the
    # constants module here would be a cycle.
    confidence: float = 0.5
    origin: RuleOrigin = RuleOrigin.CORRECTION
    superseded_by: UUID | None = None
    # The reverse history edge: a Split child names the rule it replaced; a
    # merge product has None (its parents point at it through superseded_by).
    parent_id: UUID | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    archived_at: datetime | None = None

    def declares_itself(self) -> bool:
        """True when applying this rule must be declared to the user.

        Only a staged rule declares itself: it is the hypothesis the user can
        still confirm or correct. A definitive rule is silent — the use
        attribution records it.
        """
        return self.state is RuleState.STAGING


@dataclass
class RuleUse:
    """One application of a rule: the situation and the outcome it got."""

    rule_id: UUID
    session_id: UUID
    situation: str
    outcome: UseOutcome
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    id: int | None = None
