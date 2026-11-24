"""Every module publishes its surface through one door: its package `__init__` (#189).

``interfaces`` and ``models`` stay open: they are vocabulary, not implementation.
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
    ("cortex.tools", "cortex.tools.interfaces", {"ASK_USER_CONTROL", "ASK_USER_TOOL_NAME"}),
]

# door, published name, the module that implements it, its attribute there
# (``None``: the name is that module itself).
PUBLISHED = [
    ("cortex.rules", "RuleService", "cortex.rules.service", "RuleService"),
    ("cortex.rules", "render", "cortex.rules.render", None),
    ("cortex.rules", "enqueue_turn", "cortex.rules.queue", "enqueue_turn"),
    ("cortex.goals", "GoalRepository", "cortex.goals.interfaces", "GoalRepository"),
    (
        "cortex.goals",
        "InMemoryGoalRepository",
        "cortex.goals.repository",
        "InMemoryGoalRepository",
    ),
    (
        "cortex.goals",
        "PostgresGoalRepository",
        "cortex.goals.repository",
        "PostgresGoalRepository",
    ),
    ("cortex.tools", "ASK_USER_TOOL_NAME", "cortex.tools.meta.ask_user", "ASK_USER_TOOL_NAME"),
    ("cortex.tools", "ASK_USER_CONTROL", "cortex.tools.meta.ask_user", "ASK_USER_CONTROL"),
]


@pytest.mark.parametrize(("door", "name", "implementation", "attribute"), PUBLISHED)
def test_a_door_publishes_the_object_its_implementation_holds(
    door, name, implementation, attribute
):
    """The door exposes that object, not a look-alike a caller would trip on."""
    module = importlib.import_module(implementation)
    expected = module if attribute is None else getattr(module, attribute)

    assert getattr(importlib.import_module(door), name) is expected


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
