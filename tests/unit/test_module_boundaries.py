"""The whole-map contracts hold and `shared` stays at the bottom (issues #183, #191).

Invariant I4: `layers` and `acyclic_siblings` carry no `ignore_imports` — a
module only imports downward and no runtime cycle exists between two modules.
Invariant I5: the bottom layer imports no `cortex` module at runtime; its only
`cortex` imports are annotations under `if TYPE_CHECKING:`.

Law 2 (#191): each module's implementation submodules are private, enforced by
one `protected` contract per module — never one wildcard contract, because
`cortex.*` as an importer matches every module and defeats the check.
Law 6 (#191): `shared` is a forbidden kernel, so no module may be reachable
from it at runtime.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]
SHARED = ROOT / "src" / "cortex" / "shared"

SHARED_KERNEL_CONTRACT = "shared kernel imports no module"

# Law 2's naming law: one `protected` contract per module, named `<module>
# internals are private to <module>` — the `shared` kernel is forbidden instead,
# and the two `rules:` contracts narrow a single submodule further.

# A module is entered through its package door, so these files are public on
# purpose and are never implementation to be protected.
DOOR_FILES = frozenset({"__init__.py", "interfaces.py", "models.py"})

# ADR-0023: these modules publish a surface, so each one must carry its
# contract. A module that loses its contract drops out of the coverage loop
# silently, which is the vacuity this set prevents.
DOOR_MODULES = frozenset({"memory", "rules", "tools", "goals", "trace"})


def _contracts() -> list[dict]:
    """The configured import-linter contracts, in `pyproject.toml` order."""
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return config["tool"]["importlinter"]["contracts"]


def _contract(name: str) -> dict:
    """The contract called `name`; fails loudly when it is missing."""
    for contract in _contracts():
        if contract["name"] == name:
            return contract
    raise AssertionError(f"no contract named {name!r}, found {[c['name'] for c in _contracts()]}")


def _module_contracts() -> dict[str, dict]:
    """The one module contract per module, keyed by module leaf.

    The modules come from the contracts, not from a list here; a `protected`
    contract that does not follow the naming law has no module contract to find,
    so `_contract` fails loudly instead of the module silently going unchecked.
    `DOOR_MODULES` checks the resulting set is complete, so no module can drop
    out of the coverage loop unnoticed.
    """
    covered = {
        protected.split(".")[1]
        for contract in _contracts()
        if contract["type"] == "protected"
        for protected in contract["protected_modules"]
    }
    return {leaf: _contract(f"{leaf} internals are private to {leaf}") for leaf in covered}


def test_whole_map_contracts_carry_no_exception_list():
    """Both contracts exist and neither excuses an edge (I4)."""
    whole_map = {
        contract["type"]: contract
        for contract in _contracts()
        if contract["type"] in ("layers", "acyclic_siblings")
    }
    assert set(whole_map) == {"layers", "acyclic_siblings"}, (
        f"missing whole-map contract, found {sorted(whole_map)}"
    )
    for contract in whole_map.values():
        assert "ignore_imports" not in contract, (
            f"{contract['name']} carries an exception list: {contract['ignore_imports']}"
        )


def _implementation_names(leaf: str) -> set[str]:
    """The implementation names in `cortex.<leaf>`: modules by stem, sub-packages by name.

    A sub-package such as `tools/meta/` counts as one name: its own files sit
    behind its door and are not enumerated here.
    """
    package = ROOT / "src" / "cortex" / leaf
    names = {path.stem for path in package.glob("*.py") if path.name not in DOOR_FILES}
    names |= {
        path.name for path in package.iterdir() if path.is_dir() and (path / "__init__.py").exists()
    }
    return names


def test_every_module_contract_matches_its_package():
    """Each module is entered through its door, not its implementation (Law 2).

    One contract per module, the module itself plus the composition root as the
    only allowed importers — `cortex.main` and `cortex.cli` build the concretes.
    A protected submodule is either entered or not: no contract excuses an edge,
    and none protects a wildcard, which would match every importer.

    The expected set comes from the filesystem, not from a second hand-written
    list: a file added to a protected package is unclassified until its author
    either protects it or publishes it through the module's door.
    """
    contracts = _module_contracts()
    assert set(contracts) == DOOR_MODULES, (
        f"{sorted(DOOR_MODULES - set(contracts))} lost its module contract, "
        f"{sorted(set(contracts) - DOOR_MODULES)} gained one, but ADR-0023 makes "
        f"exactly {sorted(DOOR_MODULES)} modules publish a surface"
    )

    uncovered, stale = {}, {}
    for leaf, contract in contracts.items():
        name, module = contract["name"], f"cortex.{leaf}"

        assert contract["type"] == "protected", f"{name} is not a protected contract"
        assert contract["allowed_importers"] == [module, "cortex.main", "cortex.cli"], (
            f"{name} lets someone else in: {contract['allowed_importers']}"
        )
        assert "ignore_imports" not in contract, (
            f"{name} carries an exception list: {contract['ignore_imports']}"
        )
        assert "*" not in "".join(contract["protected_modules"]), (
            f"{name} protects a wildcard, which matches every module"
        )
        assert {submodule.split(".")[1] for submodule in contract["protected_modules"]} == {leaf}, (
            f"{name} protects something outside cortex.{leaf}: {contract['protected_modules']}"
        )

        protected = {submodule.rsplit(".", 1)[-1] for submodule in contract["protected_modules"]}
        on_disk = _implementation_names(leaf)
        if missing := on_disk - protected:
            uncovered[leaf] = sorted(f"cortex.{leaf}.{name}" for name in missing)
        if gone := protected - on_disk:
            stale[leaf] = sorted(f"cortex.{leaf}.{name}" for name in gone)

    assert not uncovered, (
        f"implementation files that are neither protected nor published through the door: "
        f"{uncovered}. Add each one to its module's `protected_modules` in pyproject.toml "
        f"(private), or publish it through the module's door — `__init__.py` — as "
        f"`interfaces.py` and `models.py` already are."
    )
    assert not stale, (
        f"protected_modules names files that no longer exist: {stale}. Remove them from "
        f"pyproject.toml."
    )


def _cortex_modules() -> set[str]:
    """Every `cortex` module, excluding `shared` itself and the `__main__` bootstrap."""
    package = ROOT / "src" / "cortex"
    modules = {
        path.stem
        for path in package.glob("*.py")
        if path.stem not in ("__init__", "__main__")
    }
    modules |= {
        path.name
        for path in package.iterdir()
        if path.is_dir() and (path / "__init__.py").exists() and path.name != "shared"
    }
    return {f"cortex.{module}" for module in modules}


def test_the_shared_kernel_imports_no_module():
    """`shared` holds vocabulary, so it imports no module at runtime (Law 6)."""
    contract = _contract(SHARED_KERNEL_CONTRACT)

    assert contract["type"] == "forbidden", f"{SHARED_KERNEL_CONTRACT} is not a forbidden contract"
    assert contract["source_modules"] == ["cortex.shared"], contract["source_modules"]
    assert set(contract["forbidden_modules"]) == _cortex_modules(), (
        f"{SHARED_KERNEL_CONTRACT} leaves out "
        f"{sorted(_cortex_modules() - set(contract['forbidden_modules']))}"
    )
    assert "ignore_imports" not in contract, (
        f"{SHARED_KERNEL_CONTRACT} carries an exception list: {contract['ignore_imports']}"
    )


def _cortex_imports(tree: ast.AST) -> list[tuple[ast.AST, str]]:
    """Every `cortex.*` import node in `tree`, with its target module."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("cortex"):
            found.append((node, node.module or ""))
        elif isinstance(node, ast.Import):
            found.extend((node, alias.name) for alias in node.names if alias.name.startswith("cortex"))
    return found


def _type_only(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """True when `node` sits inside an `if TYPE_CHECKING:` block."""
    while node in parents:
        node = parents[node]
        if isinstance(node, ast.If) and ast.unparse(node.test) == "TYPE_CHECKING":
            return True
    return False


def test_shared_imports_nothing_from_cortex():
    """`shared` is the bottom layer: its cortex imports are annotations (I5)."""
    runtime, type_only = [], []
    for path in sorted(SHARED.glob("*.py")):
        tree = ast.parse(path.read_text())
        parents = {
            child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
        }
        for node, module in _cortex_imports(tree):
            target = runtime if not _type_only(node, parents) else type_only
            target.append(f"{path.name}:{node.lineno} {module}")

    assert runtime == [], f"shared imports cortex at runtime: {runtime}"
    assert {"cortex.memory.models", "cortex.rules.models"} <= {
        entry.split()[-1] for entry in type_only
    }, f"Fact/Rule annotations must stay type-only imports, got {type_only}"
