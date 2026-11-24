"""The memory bundle: what the Memory Module contributes to a reasoning step."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cortex.memory.models import Fact
    from cortex.rules.models import Rule


@dataclass
class PersonalityContext:
    """Personality traits for response formatting."""
    formality: float = 0.5  # 0 = casual, 1 = formal
    verbosity: float = 0.5  # 0 = terse, 1 = verbose
    technical_level: float = 0.5  # 0 = simple, 1 = technical
    humor_level: float = 0.5
    empathy: float = 0.5
    directness: float = 0.5


@dataclass
class AmbientContext:
    """Current ambient context (time, location, activity)."""
    time_of_day: str | None = None  # "morning", "afternoon", "evening", "night"
    day_of_week: str | None = None  # "weekday", "weekend"
    location: str | None = None
    activity: str | None = None
    weather: str | None = None


@dataclass
class MemoryContext:
    """Bundle of everything the Memory Module contributes to a reasoning step.

    `degraded_dimensions` lists the names of dimensions that failed to populate
    ("facts", "personality", "ambient", "rules"). Empty when Memory answered
    fully.
    """
    facts: list[Fact] = field(default_factory=list)
    personality: PersonalityContext | None = None
    ambient: AmbientContext | None = None
    rules: list[Rule] = field(default_factory=list)
    degraded_dimensions: list[str] = field(default_factory=list)
