# ADR-0023: The published interface is the package `__init__.py`

## Status

Accepted (2026-11-25), owner in chat on 2026-11-24 — "Mi torna il tuo
consiglio, per quella roba la risolviamo con un ticket a valle". Implemented by #189 (the doors),
#190 (the sweep), #191 (the contracts) and #192 (this record, plus the module
map in `docs/specs/architecture.md` and the term in `CONTEXT.md`).

## Context

ADR-0022 made the module map a check: nine groups, a `layers` contract and an
`acyclic_siblings` contract, no `ignore_imports`. It enforced the *order* of the
modules and said nothing about their *surfaces*. Both contracts stay green while
`services/minion_service.py` takes `cortex.memory.fact_store`,
`eval/runner.py` takes `cortex.tools.executor` and `execution/module.py`
constructs `cortex.goals.repository` itself: every one of those imports points
downward, so the map is satisfied and the module's internals are still other
modules' code.

The cost was not the ten imports themselves: it was that a rename inside a
module meant editing another module's source, and nothing would say so.
Measured on `develop` `14fe0d4` (2026-11-24) with an AST scan that skips `if
TYPE_CHECKING:` and counts function-local imports: ten runtime imports in `src/`
crossed into a submodule that is neither `interfaces` nor `models`
(`agentic/loop.py` → `tools.meta`, `agentic/reasoner.py` → `rules.render`,
`api/routes/chat.py` → `rules.queue`, `eval/runner.py` → `tools.executor`,
`tools.registry`, `memory.context_provider`, `eval/sandbox.py` → `tools.meta`,
`execution/module.py` → `goals.repository`, `services/minion_service.py` →
`memory.fact_extractor`, `memory.fact_store`). The same scan over `tests/` finds
76 more.

Half the surface already existed and nobody used it: `memory`, `trace` and
`tools` exported exactly the right names from their package `__init__.py`, and
no cross-module code imported any of them. `rules` told its readers in the
docstring to import the submodules (`cortex.rules.service`, …), and `goals` had
a docstring and no exports at all. The rule was therefore already the intended
one in three modules of five; nothing made it a rule.

**The correction that shaped the rule.** #191 measured the design's own premise
and found it false. The plan had `rules` publish `render` — the module — so that
`from cortex.rules import render` would reach `render_rules`. `grimp` resolves
that spelling as an edge on the submodule `cortex.rules.render`, so the door
spelling itself tripped the contract the door exists to satisfy: the sweep and
the protection pointed at the same file. A door publishes **names, not
namespaces**; `rules` publishes the function `render_rules`. The story is worth
keeping because it is the mistake the next module author will make —
`from cortex.memory import fact_store` reads like the door and is not.

## Decision

1. **The door is the package `__init__.py`.** `from cortex.memory import
   ContextProvider` is how another module enters `memory`. No new layer, no new
   file per module: the package body already runs on every submodule import, so
   the door adds no import edge of its own — it only decides which names a
   consumer may name.
2. **`interfaces` and `models` stay open.** They are the vocabulary modules
   share, not an implementation. Protecting them would force every door to
   re-export every dataclass, which is the second implementation this decision
   rejects elsewhere.
3. **One `protected` contract per module**, `memory`, `rules`, `tools`, `goals`
   and `trace`, in the shape `rules` already used: `protected_modules` lists the
   implementation submodules, and `allowed_importers` names the module itself,
   `cortex.main` and `cortex.cli`. The composition root may import concretes —
   building them is its job — and a module may import its own internals. No
   contract carries `ignore_imports`.
4. **`shared` is a forbidden kernel.** One `forbidden` contract: `cortex.shared`
   imports no `cortex` module at runtime. Its `Fact`, `Rule` and `UsageStats`
   annotations are dropped by `exclude_type_checking_imports`, so Law 6 is
   green without an exception.
5. **The door publishes names, not namespaces.** A re-exported class or
   function, never a submodule: the submodule spelling is an edge on the very
   file the contract protects.
6. **Tests stay outside the contracts.** `root_package = "cortex"`, so the 76
   test imports into internals keep working and Law 7 stays a review step. A
   test fakes the seam it replaces, which is a reason to reach in, not a defect
   to sweep.
7. **`pyproject.toml` is the single source.** The AST scan that carried a
   hand-written private-submodule list was deleted at #191;
   `tests/unit/test_module_boundaries.py` reads the contracts out of the config
   and asserts their shape, and the module map in
   `docs/specs/architecture.md` is a view of the same names.

Measured after #191: `uv run lint-imports` → **10 contracts kept, 0 broken**
(two whole-map, five per-module `protected`, one `forbidden` for `shared`, and
the two legacy `rules` contracts — see the last rejected alternative). The ten
sites moved to the doors in #190, and `ExecutionModule` lost the fallback that
constructed an in-memory goal repository: `goal_repository` is a required
parameter, so no module can wire another module's implementation even by
accident.

## Alternatives rejected

- **A `facade.py` / `api.py` per module.** A second file to keep in sync with
  the `__init__.py` that already exists and already carried the re-exports in
  three modules of five. It also makes "the module has a surface" mean "someone
  created a file" — a layout convention, not a boundary — and the two files can
  disagree with nothing to compare them.
- **One wildcard contract.** `protected_modules = ["cortex.*.service"]` with
  `allowed_importers = ["cortex.*"]` reads like a single rule over every module,
  but every importer matches the wildcard, so every module keeps entering every
  other's internals: a green check that checks nothing. The `protected` type
  treats `cortex.*` as an expression, not as "any module except the owner".
- **`root_packages = ["cortex", "tests"]`, so the contracts also cover the 76
  test imports.** It looks like finishing the job and is a second migration with
  no defect behind it: tests are allowed to fake the seam they replace, the
  sweep would touch 76 imports to buy a check whose subject is `src/`, and
  failing contract reports would start arriving from the test suite, where the
  fix is usually to import the submodule on purpose. Law 7 is deferred, not
  forgotten.
- **Consolidating the two legacy `rules` `protected` contracts into the new
  per-module one.** The new contract admits `cortex.main` and `cortex.cli` to
  `cortex.rules.scoring`; the existing scoring contract admits `cortex.rules`
  only. Merging them would relax an invariant to remove a duplicate, so the pair
  stays and keeps its stricter list.

## Consequences

- **A new module is born private.** Adding `cortex/foo/` makes its submodules
  reachable until someone copies the contract — so the copy is part of adding a
  module: one `protected` contract in `pyproject.toml` and one line in the
  table in `docs/specs/architecture.md`. The document and the config move
  together, as ADR-0022 required of the layer list.
- **An import that used to be legal is red in CI.** `from cortex.memory import
  fact_store` and `from cortex.tools import registry` are contract failures
  now, and the report names the importer and the protected module. The ten
  sites that did it were fixed in #190, so the check shipped green with no
  exception list.
- **Tests stay outside, `TYPE_CHECKING` stays free.** The 76 test imports into
  internals are untouched, and an annotation alias still cannot fail a boundary:
  `exclude_type_checking_imports = true` is a session option.
- **The door can create a cycle, and the door rule is what stops it.** A
  submodule import executes the parent `__init__`, so a re-export that reaches
  upward is a hard upward edge — the `layers` contract goes red on the package
  edge, not on the file that looks guilty. That is why the door publishes "only
  what a consumer needs" and not everything: the safest door is a short one.
- **Reading a module starts in one file.** The `__init__.py` says what the
  module offers; the contract says what it is allowed to keep to itself; the
  module map says where it sits. Three answers, one edit apart.
