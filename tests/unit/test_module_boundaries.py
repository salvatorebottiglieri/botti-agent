"""The whole-map contracts hold and `shared` stays at the bottom (issue #183).

Invariant I4: `layers` and `acyclic_siblings` carry no `ignore_imports` — a
module only imports downward and no runtime cycle exists between two modules.
Invariant I5: the bottom layer imports no `cortex` module at runtime; its only
`cortex` imports are annotations under `if TYPE_CHECKING:`.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]
SHARED = ROOT / "src" / "cortex" / "shared"


def test_whole_map_contracts_carry_no_exception_list():
    """Both contracts exist and neither excuses an edge (I4)."""
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    contracts = config["tool"]["importlinter"]["contracts"]
    whole_map = {
        contract["type"]: contract
        for contract in contracts
        if contract["type"] in ("layers", "acyclic_siblings")
    }
    assert set(whole_map) == {"layers", "acyclic_siblings"}, (
        f"missing whole-map contract, found {sorted(whole_map)}"
    )
    for contract in whole_map.values():
        assert "ignore_imports" not in contract, (
            f"{contract['name']} carries an exception list: {contract['ignore_imports']}"
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
