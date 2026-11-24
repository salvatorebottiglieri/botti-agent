"""Every module publishes its surface through one door: its package `__init__` (#189).

The door is the package body, so each name below must resolve from
``cortex.<module>`` *and* be the object the implementation module holds — a door
that exported a look-alike would pass an existence check and fail a caller.
``interfaces`` and ``models`` stay open: they are vocabulary, not implementation,
so ``from cortex.rules.models import Rule`` keeps working.
"""

from __future__ import annotations

import importlib

import pytest

# door -> the submodule that stays open, and the names the door publishes.
SURFACES = [
    ("cortex.rules", "cortex.rules.models", {"RuleService", "render", "enqueue_turn"}),
    (
        "cortex.goals",
        "cortex.goals.models",
        {"GoalRepository", "InMemoryGoalRepository", "PostgresGoalRepository"},
    ),
    ("cortex.tools", "cortex.tools.interfaces", {"ASK_USER_TOOL_NAME"}),
]


def test_rules_door_publishes_the_rule_service():
    """`RuleService` comes from the door, and is the service's own class."""
    from cortex.rules import RuleService

    implementation = importlib.import_module("cortex.rules.service")

    assert RuleService is implementation.RuleService


def test_rules_door_publishes_the_render_module():
    """`render` is the rendering namespace itself, not a copy or a single call."""
    from cortex.rules import render

    implementation = importlib.import_module("cortex.rules.render")

    assert render is implementation


def test_rules_door_publishes_enqueue_turn():
    """`enqueue_turn` comes from the door, and is the queue's own function."""
    from cortex.rules import enqueue_turn

    implementation = importlib.import_module("cortex.rules.queue")

    assert enqueue_turn is implementation.enqueue_turn


def test_goals_door_publishes_the_repository_interface():
    """`GoalRepository` is the interface `execution` depends on."""
    from cortex.goals import GoalRepository

    implementation = importlib.import_module("cortex.goals.interfaces")

    assert GoalRepository is implementation.GoalRepository


def test_goals_door_publishes_the_in_memory_repository():
    """`InMemoryGoalRepository` is the implementation the tests inject."""
    from cortex.goals import InMemoryGoalRepository

    implementation = importlib.import_module("cortex.goals.repository")

    assert InMemoryGoalRepository is implementation.InMemoryGoalRepository


def test_goals_door_publishes_the_postgres_repository():
    """`PostgresGoalRepository` is the implementation the root builds."""
    from cortex.goals import PostgresGoalRepository

    implementation = importlib.import_module("cortex.goals.repository")

    assert PostgresGoalRepository is implementation.PostgresGoalRepository


def test_tools_door_publishes_the_ask_user_tool_name():
    """`ASK_USER_TOOL_NAME` is the constant the tool and the loop share."""
    from cortex.tools import ASK_USER_TOOL_NAME

    implementation = importlib.import_module("cortex.tools.meta.ask_user")

    assert ASK_USER_TOOL_NAME is implementation.ASK_USER_TOOL_NAME


@pytest.mark.parametrize(("door", "open_submodule", "names"), SURFACES)
def test_each_door_lists_what_it_publishes(door, open_submodule, names):
    """`__all__` names the surface, and the package binds every name it lists.

    The open submodule is imported first: the package body runs on *every*
    submodule import (spec P5), so the door cannot be a lazy afterthought.
    """
    importlib.import_module(open_submodule)
    door_module = importlib.import_module(door)

    assert names <= set(door_module.__all__)
    assert set(door_module.__all__) <= set(vars(door_module))


def test_the_open_vocabulary_is_still_importable_directly():
    """`models` stays open: the shared vocabulary is not forced through the door."""
    from cortex.goals.models import Goal
    from cortex.rules.models import Rule

    assert (Rule.__name__, Goal.__name__) == ("Rule", "Goal")
