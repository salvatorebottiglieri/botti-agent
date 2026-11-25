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

# The five per-module contracts #191 adds (the sixth, the `shared` kernel, is
# forbidden rather than protected): module -> the implementation submodules
# private to it, relative to `cortex.<module>`. Each contract is
# `f"{module} internals are private to {module}"`. `interfaces` and `models` are
# absent on purpose: they are vocabulary, not implementation, so they stay open.
MODULE_CONTRACTS = {
    "memory": (
        "context_provider", "fact_store", "fact_extractor", "conversation_extractor",
        "evidence_store", "repository", "backfill",
    ),
    "rules": (
        "service", "queue", "render", "scoring", "repository",
        "attribution", "conflicts", "feedback", "splits", "constants",
    ),
    "tools": ("executor", "registry", "meta"),
    "goals": ("repository",),
    "trace": ("recorder", "audit", "repository", "pseudonymizer"),
}

SHARED_KERNEL_CONTRACT = "shared kernel imports no module"


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


def test_every_module_contract_protects_its_implementation():
    """Each module is entered through its door, not its implementation (Law 2).

    One contract per module, the module itself plus the composition root as the
    only allowed importers — `cortex.main` and `cortex.cli` build the concretes.
    A protected submodule is either entered or not: no contract excuses an edge,
    and none protects a wildcard, which would match every importer.
    """
    for leaf, implementation in MODULE_CONTRACTS.items():
        name, module = f"{leaf} internals are private to {leaf}", f"cortex.{leaf}"
        contract = _contract(name)

        assert all("." not in submodule for submodule in implementation), (
            f"{name} names a submodule of another module"
        )
        assert contract["type"] == "protected", f"{name} is not a protected contract"
        assert contract["allowed_importers"] == [module, "cortex.main", "cortex.cli"], (
            f"{name} lets someone else in: {contract['allowed_importers']}"
        )
        assert set(contract["protected_modules"]) == {
            f"{module}.{submodule}" for submodule in implementation
        }, f"{name} protects {sorted(contract['protected_modules'])}"
        assert "ignore_imports" not in contract, (
            f"{name} carries an exception list: {contract['ignore_imports']}"
        )
        assert "*" not in "".join(contract["protected_modules"]), (
            f"{name} protects a wildcard, which matches every module"
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
