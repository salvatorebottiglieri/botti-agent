# ADR-0022: Module layers enforced by a check, and a `shared` seam at the bottom

## Status

Accepted (2026-11-24), owner in chat — "piano ok, A": A = a `cortex/shared/`
module for the context bundle and the loop-event types, `Goal` in `goals`, and
`eval` declared above `agentic` in the layer scale. Implemented by #181
(`shared`), #182 (`Goal`) and #183 (the contracts); #184 reconciles
`docs/specs/architecture.md` with the check.

## Context

Cortex was a modular monolith by document only. `docs/specs/architecture.md`
numbered the modules "Layer 0" to "Layer 6" and drew the arrows between them,
and nothing checked either: CI ran pytest, ruff, mypy and exactly two
`protected` import contracts, and both are about the rule system
(`cortex.rules.repository` and `cortex.rules.scoring` are private to `rules`).
Nothing stopped a module from importing upward, and nothing stopped two modules
from becoming inseparable. The layer map was a wish, checked by reading it.

The cost was already paid. Measured on `6ae1898` (2026-11-24) by an AST scan that
excludes `TYPE_CHECKING` imports: 81 module-pair edges among 21 modules, exactly
one two-module cycle (`agentic ↔ memory`), and four edges reaching up the
documented layers (`memory`, `goals`, `trace`, `eval` → `agentic`). The cycle was
the expensive one: the context bundle (`MemoryContext`) and the loop's progress
events (`LoopEvent`) — the two types the whole system speaks — lived inside
`agentic`, so `memory` could not be read, tested or extracted without the loop;
`Goal` lived there too, so the goal repository reached into the agentic core;
and `trace` and `eval` imported the loop for its event types while the diagram
already drew them as its consumers.

The issue that scoped this work also named a second cycle,
`config → llm → tools → config`. It never existed at runtime: `config → llm` is a
single `UsageStats` annotation under `if TYPE_CHECKING:`, and `config` has no
outbound runtime edge at all. What kept the claim alive was a comment in
`pyproject.toml` repeating it, not the code. Fixing the measurement — deciding
what counts as an edge — was part of this decision, and it removed work instead
of adding it.

## Decision

1. **Nine groups, `shared` at the bottom.** `[tool.importlinter]` in
   `pyproject.toml` holds this order, top to bottom:

   | # | Group | Members |
   |---|---|---|
   | 1 | integration | `main`, `cli` |
   | 2 | orchestration | `api`, `execution`, `interaction` |
   | 3 | consumers | `eval`, `trace` |
   | 4 | agentic core | `agentic` |
   | 5 | services | `services` |
   | 6 | modules | `sessions`, `memory`, `validation`, `goals`, `minions`, `rules`, `learning` |
   | 7 | primitives | `events`, `llm`, `tools`, `db` |
   | 8 | foundation | `config`, `logging`, `safety` |
   | 9 | shared | `shared` |

   A module may import a group below it. Members of one group may import each
   other only while that closes no cycle: `:` in the `layers` list marks a group
   whose members may talk to each other, `|` marks siblings that may not
   (`main | cli`).
2. **`eval` and `trace` sit above `agentic`.** The order follows who drives
   whom, not the folder names: the eval harness builds a real `AgentLoop` per
   task, and the trace recorder consumes the `LoopEvent` stream the loop emits.
   A consumer below what it drives would have to be imported by it. The old
   numbers called both of them Layer 2 under a Layer 4 loop — the inverse of the
   `Eval --> Loop` arrow the diagram already drew.
3. **`shared` holds the vocabulary the system speaks, not the implementation.**
   `shared/bundle.py` carries `MemoryContext`, `PersonalityContext` and
   `AmbientContext`; `shared/loop_events.py` carries `LoopEvent` and its seven
   subclasses. Its only `cortex` imports are `Fact`, `Rule` and `UsageStats`
   under `if TYPE_CHECKING:`. `agentic` keeps the loop, the reasoner, the
   executor and `Decision`, `Context`, `ChatResponse`, `GoalResult`;
   `Goal`, `GoalStep` and `GoalStatus` live in `goals` (#182). The cycle becomes
   `memory → shared` plus `agentic → memory`, and `api`, `trace` and `eval` take
   the events from `shared` instead of from the loop.
4. **A type-only import is not an edge, for every contract.**
   `exclude_type_checking_imports = true` is set once under
   `[tool.importlinter]`, as a session option, so an annotation alias can neither
   fail a boundary nor invent a cycle.
5. **Two whole-map contracts, no exception list.** A `layers` contract over the
   nine groups and an `acyclic_siblings` contract over the same groups, both with
   **zero** `ignore_imports`. Each was proven red by a control violation before
   it was kept — an upward import, then a two-module cycle — the same recipe the
   two `protected` contracts used. The two `protected` rule contracts stay as
   they are.

## Alternatives rejected

- **An `ignore_imports` list.** It turns design debt into a passing check: the
  contracts would ship green with `memory ↔ agentic` still in the code, and an
  entry that made CI green has no owner to remove it. The two `protected`
  contracts are worth something precisely because their lists are empty.
- **Keep the bundle in `agentic` and except `memory → agentic`.** The cycle this
  decision exists to remove stays, and the exception is permanent: memory will
  always need the bundle it fills.
- **Inject a loop factory into `eval` so the harness does not import the loop it
  exists to drive.** A port built for a test harness whose job is to run the real
  loop, and it hides exactly the dependency the check exists to make visible.
- **Leave the second cycle in the `pyproject.toml` comment.** The
  `config → llm → tools → config` "cycle" was wrong — a `TYPE_CHECKING` import,
  never a runtime edge. A comment in the contract file describing a cycle that
  does not exist sends the next reader to fix a problem that is not there.

## Consequences

- **A module is placed before it imports.** Adding `cortex/foo/` and importing
  upward keeps the contracts green until `foo` is named in the `layers` list —
  measured: an unlisted `cortex.newmod` importing `cortex.main` leaves four
  contracts kept. `acyclic_siblings` does see it, because it covers every child
  of `cortex` (naming the reverse edge breaks it). Placement is therefore a
  review duty; the check cannot infer the group a new module belongs to.
- **A same-group import is legal only if acyclic.** Measured: `memory` importing
  `validation` (both group 6) keeps `layers` and breaks `acyclic_siblings`
  (`.memory -> .validation`), so the `:` groups cannot silently become blobs.
- **The `Boundaries` job fails on an upward runtime import or a two-module
  cycle.** Measured: `shared` importing `memory` breaks `layers`
  ("cortex.shared is not allowed to import cortex.memory") and
  `acyclic_siblings`.
- **A `TYPE_CHECKING` alias is free.** The same upward import placed under
  `if TYPE_CHECKING:` keeps all four contracts green, so a settings type does not
  have to move for an annotation.
- **The document and the config move together.** The nine groups in
  `docs/specs/architecture.md` and the `layers` list in `pyproject.toml` are the
  same names in the same order; a change to one without the other is how this
  drift started.
- **`agentic` is smaller, not more central.** It keeps what the loop does and
  loses what the system says; `memory`, `api`, `trace` and `eval` can import the
  bundle and the events without importing the loop, and `goals` no longer
  imports `agentic` at all.
