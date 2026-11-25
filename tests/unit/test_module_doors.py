"""Every module publishes its surface through one door: its package `__init__` (#189).

``interfaces`` and ``models`` stay open: they are vocabulary, not implementation.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"

# door -> the submodule that stays open, and the names the door publishes.
SURFACES = [
    ("cortex.rules", "cortex.rules.models", {"RuleService", "render", "enqueue_turn"}),
    (
        "cortex.goals",
        "cortex.goals.models",
        {"GoalRepository", "InMemoryGoalRepository", "PostgresGoalRepository"},
    ),
    (
        "cortex.memory",
        "cortex.memory.models",
        {"ContextProvider", "FactStore", "SUPPORTED_EVENT_TYPES"},
    ),
    (
        "cortex.tools",
        "cortex.tools.interfaces",
        {"ASK_USER_CONTROL", "ASK_USER_TOOL_NAME", "AskUserTool"},
    ),
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
    ("cortex.tools", "AskUserTool", "cortex.tools.meta.ask_user", "AskUserTool"),
    (
        "cortex.memory",
        "SUPPORTED_EVENT_TYPES",
        "cortex.memory.fact_extractor",
        "SUPPORTED_EVENT_TYPES",
    ),
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


# The five modules whose implementation submodules are private (#190).
CONSUMER_PACKAGES = ("agentic", "api", "eval", "execution", "services")
PRIVATE_SUBMODULES = (
    "cortex.goals.repository",
    "cortex.memory.context_provider",
    "cortex.memory.conversation_extractor",
    "cortex.memory.evidence_store",
    "cortex.memory.fact_extractor",
    "cortex.memory.fact_store",
    "cortex.memory.repository",
    "cortex.rules.queue",
    "cortex.rules.render",
    "cortex.rules.service",
    "cortex.tools.executor",
    "cortex.tools.meta",
    "cortex.tools.registry",
    "cortex.trace.recorder",
)


def _is_type_checking_guard(test: ast.expr) -> bool:
    """`if TYPE_CHECKING:` / `if typing.TYPE_CHECKING:`."""
    match test:
        case ast.Name(id="TYPE_CHECKING"):
            return True
        case ast.Attribute(attr="TYPE_CHECKING"):
            return True
    return False


def _imported_modules(tree: ast.AST) -> list[tuple[int, str]]:
    """Every module imported by ``tree`` as (line, dotted name), runtime only.

    ``if TYPE_CHECKING:`` blocks are skipped: they are annotation-only and the
    sweep measures runtime imports (spec P2 measures the same way).
    """
    imported: list[tuple[int, str]] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.If) and _is_type_checking_guard(child.test):
                continue
            if isinstance(child, ast.ImportFrom) and child.module:
                imported.append((child.lineno, child.module))
            elif isinstance(child, ast.Import):
                imported.extend((child.lineno, alias.name) for alias in child.names)
            visit(child)

    visit(tree)
    return imported


def test_consumers_enter_the_private_modules_through_their_door_only():
    """No consumer package imports `cortex.<module>.<private>` (#190).

    The door is the package `__init__`; `interfaces` and `models` stay open.
    """
    violations: list[str] = []
    scanned = 0
    for package in CONSUMER_PACKAGES:
        for path in sorted((SRC / "cortex" / package).rglob("*.py")):
            scanned += 1
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for line, name in _imported_modules(tree):
                if any(
                    name == private or name.startswith(f"{private}.")
                    for private in PRIVATE_SUBMODULES
                ):
                    violations.append(f"{path.relative_to(SRC)}:{line} imports {name}")

    assert scanned > 0, f"no sources found under {SRC}"
    assert violations == []
