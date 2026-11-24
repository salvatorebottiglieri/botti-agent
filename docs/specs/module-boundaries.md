---
feature: module-boundaries
repo: salvatorebottiglieri/botti-agent
branch: feat-167-module-boundaries
tracker: https://github.com/salvatorebottiglieri/botti-agent/issues/167
---

# Module boundaries — a fitness function with no exception list

## Phase plan

| Phase | Decision | Reason |
|---|---|---|
| 1 · Design & alignment | run | the issue names a target (`layers` + `acyclic_siblings`, no `ignore_imports`) but not the model: which edges are legal by design (the diagram draws `Eval --> Loop` and `Memory --> Rules`) and which are debt; `MemoryContext` moving out of `agentic` is a contract change with its own blast radius |
| 2 · Product review | skip | nothing is shown to the user: CI contracts, imports and docs; no UI surface |
| 3 · System architecture | run | this feature *is* architecture: the layer model, the shared types and the cycle burn-down |
| 4 · Tickets | run | — |
| 5 · Program design | run | — |
| 6 · Implement-loop | run | — |

Approved: 2026-11-24, owner in chat — "piano ok, A" (phases as above; A = a `cortex/shared/` module for the bundle and loop-event types, `Goal` in `goals`, `eval` declared above `agentic` in the layer scale).

## Problem Statement

The architecture document says Cortex is a modular monolith with ordered layers.
CI does not: it runs pytest, ruff, mypy and exactly two import contracts
(`cortex.rules.repository` and `cortex.rules.scoring` are private to
`rules`). Nothing stops a module from importing upward, and nothing stops two
modules from becoming inseparable again. The layer map is a wish, checked by
reading.

The cost is not theoretical. `memory` and `agentic` import each other at
runtime, so neither can be read, tested or extracted alone: the context bundle
(`MemoryContext`) and the loop's events (`LoopEvent`) — the two contracts the
whole system speaks — live inside the loop. `goals` reaches into the loop for
`Goal`. `trace` reaches into the loop for the events it records, and `eval`
reaches into the loop to drive it. Every one of those edges is real today and
none of them is visible to CI.

The issue also names a cycle that is not one: `config → llm` is a
`TYPE_CHECKING` import, so a runtime contract never sees it. Fixing the
measurement first (what counts as an edge) is part of this feature, and it
removes work from the issue rather than adding it.

## Solution

Give the shared contracts a home below every module, then make the layer order
a check with an empty exception list.

- **A `shared` layer.** `cortex/shared/` holds the types that are nobody's
  implementation: `bundle.py` (`MemoryContext`, `PersonalityContext`,
  `AmbientContext`) and `loop_events.py` (`LoopEvent` and its seven
  subclasses). `Goal`, `GoalStep`, `GoalStatus` move to `goals`, which is where
  a goal lives.
- **The cycle goes away by construction.** `memory` imports the bundle from
  `shared`; `agentic` imports it from `shared` too. `trace` and `eval` import
  the events from `shared`. After the move the only edge between `memory` and
  `agentic` is `agentic → memory` (the loop reads the bundle), and the runtime
  graph has no cycle.
- **`eval` is above `agentic` in the scale, by declaration.** The eval harness
  drives the real loop on purpose — the architecture diagram already draws
  `Eval --> Loop`. Declaring it above is honest; adding it to an exception list
  would not be.
- **Type-only imports do not count as edges.** Both contracts set
  `exclude_type_checking_imports`, so an annotation alias cannot fail a runtime
  boundary and cannot invent a cycle.
- **Two contracts, zero `ignore_imports`.** A `layers` contract in the order
  `integration → orchestration → consumers (eval, trace) → agentic → services →
  modules → primitives → foundation → shared`, and an `acyclic_siblings` for
  the same groups. Each is proven red by a control violation before it is kept.
- **The document follows the check.** `docs/specs/architecture.md` is
  reconciled with the enforced order — including `Eval --> Loop` as a declared
  layer, not an exception.

Behaviour does not change: the bundle keeps its four dimensions and the rule
system keeps its 40-rule budget, its validity window and its states. This is a
move of type definitions and the wiring of two CI contracts.

## User Stories

1. As the maintainer, I want CI to fail when a module imports upward, so the
   layer map is a check and not a document.
2. As the maintainer, I want CI to fail on a runtime cycle, so two modules
   cannot become inseparable again.
3. As a module author, I want the context-bundle types importable without
   importing the loop, so `memory` does not depend on `agentic`.
4. As a module author, I want `LoopEvent` importable without importing the
   loop, so `trace` and `eval` consume the stream's contract instead of the
   orchestrator.
5. As the goals owner, I want `Goal` to live in `goals`, so its repository does
   not reach into the loop's models.
6. As the eval owner, I want the loop-driving edge declared legal (`eval` above
   `agentic`), so a real harness is not an exception list.
7. As the maintainer, I want type-only imports not to count as edges, so a
   `TYPE_CHECKING` alias cannot fail a boundary or invent a cycle.
8. As the maintainer, I want each contract proven red by a control violation,
   so a green run means something.
9. As a reader, I want `architecture.md` to match the enforced order, so the
   diagram is the truth.
10. As the maintainer, I want one move per cycle documented, so a regression
    names the edge it broke.
11. As the owner, I want the bundle's four dimensions and their degradation
    unchanged, so memory behaviour is untouched.
12. As the owner, I want the rule pipeline untouched, so the 40-rule budget,
    the validity window and the states behave as before.
13. As a test author, I want the existing imports updated with no behavioural
    change, so the full suite stays green.
14. As the maintainer, I want no `ignore_imports` anywhere in the two
    contracts, so the debt is burned down rather than excepted.

## Implementation Decisions

- **`cortex/shared/`**: a new module below every other, holding only
  definitions. `bundle.py` carries `MemoryContext` (facts, personality,
  ambient, rules, degraded dimensions), `PersonalityContext`, `AmbientContext`;
  `loop_events.py` carries `LoopEvent`, its seven subclasses, and the
  `to_dict` contract. Its only imports are the standard library and, under
  `TYPE_CHECKING`, `Fact`/`Rule` for annotations.
- **`agentic` keeps the loop, not the types.** `agentic/models.py` and
  `agentic/events.py` stop defining the moved types and import them from
  `shared`; no re-export shim (call sites are updated, not aliased), because an
  alias would leave the old edge in place.
- **`Goal`, `GoalStep`, `GoalStatus` move to `goals/models.py`.** `agentic`
  imports them from `goals` (a downward edge); `goals` stops importing
  `agentic` entirely.
- **`eval` sits above `agentic`.** The harness builds a real `AgentLoop` inside
  a sandbox; the layer scale says so, and the architecture diagram already
  draws it.
- **Contracts**: `layers` (ordered groups) and `acyclic_siblings`, both with
  `exclude_type_checking_imports`, both with no `ignore_imports`. Groups:
  `shared`; `config | logging | safety | db`; `events | llm | tools`;
  `sessions | memory | validation | goals | minions | rules | learning`;
  `services`; `agentic`; `eval | trace`; `api | execution | interaction`;
  `main | cli`.
- **The scale is derived from the call direction, not from the folder names**:
  a module that *drives* another sits above it. `eval` and `trace` drive or
  consume the loop; `api`/`execution` drive the modules; `main` drives
  everything.

## Testing Decisions

- **One seam: the CI `Boundaries` job.** `uv run lint-imports` is the check and
  the only new verification surface; no new unit-test seam is introduced.
- Each contract is proven red by a control violation (an upward import, then a
  two-module cycle) executed and reverted before the contract is kept — the
  same recipe the two protected contracts already used.
- The premise measurement (the runtime-only AST scan) is a one-off used in this
  spec; it is not committed, because `lint-imports` is the standing check.
- The full suite (1.871 tests) and the `Boundaries` job stay green; the move is
  import-only, so no test behaviour changes.

## Premises

Measured on `develop` `6ae1898` (2026-11-24) by this session and independently
re-checked, read-only, by a scout in a fresh context (the corrections below are
the scout's; where it says *falsified*, the claim died and the design changed).

| # | Premise | Command | Output | Verdict |
|---|---|---|---|---|
| P1 | CI enforces exactly two import contracts, both `protected`, and has no `layers`/`acyclic_siblings` | `sed -n '/\[tool.importlinter\]/,$p' pyproject.toml`; `rg -n "lint-imports" .github/workflows/ci.yml` | two `protected` contracts (`cortex.rules.repository`, `cortex.rules.scoring`); the `Boundaries` job runs `uv run lint-imports`; no other contract type | confirmed |
| P2 | `config → llm` and `interaction → execution` are `TYPE_CHECKING`-only, not runtime edges | `sed -n 8,16p src/cortex/config/llm.py`; `sed -n 6,14p src/cortex/interaction/service.py` | the imports sit inside `if TYPE_CHECKING:`; whole-package greps show no other runtime reference | confirmed |
| P3 | Excluding type-checking imports, the module graph has exactly one two-module cycle: `agentic ↔ memory` | AST scan over `src/cortex/**/*.py` (this session) | 81 module-pair edges among 21 modules; 2-cycles: `[('agentic','memory')]` | confirmed |
| P4 | `memory`, `goals`, `trace` and `eval` import `cortex.agentic` at runtime | `rg -n "from cortex.agentic" src/cortex/{memory,goals,trace,eval}` | `memory/context_provider.py:9`; `goals/interfaces.py:6`, `goals/repository.py:14`; `trace/recorder.py:48`, `trace/audit.py:39`; `eval/runner.py:22-26` (also `Reasoner`) | confirmed |
| P5 | The bundle types live in `agentic/models.py` and, outside `agentic`, only `memory/context_provider.py` imports them at runtime | all `MemoryContext`/`PersonalityContext`/`AmbientContext` importers | definitions in `agentic/models.py`; module-level importer `memory/context_provider.py:9`, **plus a function-local `MemoryContext` import at `eval/runner.py:172`** | falsified |
| P6 | Moving `Goal`/`GoalStep`/`GoalStatus` to `goals` leaves no importer outside `agentic`/`goals` | all importers | `goals/interfaces.py:6`, `goals/repository.py:14` **and `execution/module.py:13-19` (runtime)**; `GoalStep` has no importer outside `agentic` | falsified |
| P7 | `LoopEvent` and its seven subclasses live in `agentic/events.py`; outside importers are `trace` and `eval` | `rg -n "from cortex.agentic.events" src/cortex` | `trace/recorder.py:48`, `trace/audit.py:39`, `eval/runner.py:23`, **and `api/routes/chat.py:13` (runtime)**; `execution/module.py:24` is type-only | confirmed |
| P8 | The installed import-linter (2.17) supports excluding type-only imports | grep the installed package | the option is `exclude_type_checking_imports`, read from **`session_options`** — it belongs under the top-level `[tool.importlinter]` table and applies to every contract, not inside a `[[contracts]]` block | confirmed |
| P9 | The architecture spec already draws `Eval --> Loop` | `rg -n "Eval -->" docs/specs/architecture.md` | the arrow exists, **but the document numbers `agentic` as Layer 4 and `eval` as Layer 2** — the numeric layering is the inverse of the arrow | falsified |
| P10 | The issue's `config → llm → tools → config` is a runtime cycle | `sed -n 8,16p src/cortex/config/llm.py` | `config → llm` is inside `if TYPE_CHECKING:`, so no runtime edge; `config` has zero outbound runtime edges. The cycle exists only when type-only imports are counted — and the stale comment in `pyproject.toml:84-90` is what repeats it | falsified |

The falsifications change the design in three places: the move list gains
`eval/runner.py`, `execution/module.py` and `api/routes/chat.py`; the
type-checking exclusion is a global setting, not a per-contract one; and the
architecture document's numeric layering must be rewritten, not merely cited.

## Tickets

- #181 — shared: the bundle and the loop events move out of `agentic` (blocked by: none)
- #182 — goals: `Goal`, `GoalStep`, `GoalStatus` move to `goals` (blocked by: none)
- #183 — the `layers` and `acyclic_siblings` contracts, no exception list (blocked by: #181, #182)
- #184 — the architecture document and ADR follow the enforced order (blocked by: #183)


## System Architecture

Classification: modular-monolith boundary work. No endpoint, no stored data and
no new runtime flow: the artifacts are the enforced module map and the laws it
holds. The two protected contracts stay as they are; this feature adds the two
whole-map contracts that `pyproject.toml` deliberately left out.

### The enforced order

From top to bottom; a group may import a lower group, and a same-group import is
legal only if it does not close a cycle (`acyclic_siblings`).

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

`eval` and `trace` sit above `agentic` because they *drive or consume* the loop
(the harness builds a real `AgentLoop`; the recorder consumes its event stream).
That is the declaration the document owes the arrow it already draws.

### Cycle burn-down

| Edge today | Move | Edge after |
|---|---|---|
| `memory → agentic` (bundle types, `context_provider.py:9`) | `MemoryContext`/`PersonalityContext`/`AmbientContext` → `cortex/shared/bundle.py` | `memory → shared`, `agentic → shared` |
| `trace → agentic` (`LoopEvent`, `recorder.py:48`, `audit.py:39`) | `LoopEvent` + seven subclasses → `cortex/shared/loop_events.py` | `trace → shared` |
| `eval → agentic` for the events (`runner.py:23`) and a local `MemoryContext` (`runner.py:172`) | import from `shared` | `eval → shared`; the `eval → agentic` edge remains, legal by layer 3 |
| `goals → agentic` (`Goal`, `GoalStatus`) | move the classes to `cortex/goals/models.py`; update `execution/module.py:13-19` | `agentic → goals` (downward), `execution → goals` |
| `api → agentic` for `LoopEvent` (`chat.py:13`) | import from `shared` | `api → shared` |

`agentic → memory` stays: the loop reads the bundle. It is a downward edge and
the only one between the two modules after the move.

### Contracts

- `[tool.importlinter]` gains the global `exclude_type_checking_imports = true`,
  so an annotation alias is not a runtime edge and cannot invent a cycle.
- `layers`: the nine groups above, in order.
- `acyclic_siblings`: no cycle among the modules of one group.
- Both with no `ignore_imports`; each proven red by a control violation before
  it is kept (an upward import; then a two-module cycle).
- The stale comment in `pyproject.toml:84-90` (the second "measured cycle") is
  corrected in the same change.

### Module invariants

| Module | Owns | May not |
|---|---|---|
| `shared` | the bundle and loop-event definitions | import any other `cortex` module at runtime |
| `memory` | facts, evidence, retrieval, the bundle seam | import `agentic` |
| `agentic` | the loop, the reasoner, the executor | be imported by `memory` or `goals` |
| `goals` | `Goal`, `GoalStep`, `GoalStatus`, their repository | import `agentic` |
| `trace` | pseudonymized event persistence and audit | import `agentic` for the event types (they live in `shared`) |
| `eval` | the harness | import `orchestration` or `integration` |
| `api` | HTTP contracts | import `agentic` for the event types (they live in `shared`) |

### System invariants

| Law | Negation → test | Verified in |
|---|---|---|
| **I1** — a runtime import only points down the nine groups | one module imports a module in a higher group and the contract fails | `pyproject.toml` `layers` contract, run by `lint-imports` in CI |
| **I2** — no runtime cycle between two modules, and none among a group's siblings | two modules in one group import each other and the contract fails | `pyproject.toml` `acyclic_siblings` contract |
| **I3** — a `TYPE_CHECKING` import is not an edge | a type-only upward import fails the contract | `[tool.importlinter]` `exclude_type_checking_imports = true`, proven by the negative control |
| **I4** — the contracts carry no exception list | an `ignore_imports` appears in either whole-map contract | `tests/unit/test_module_boundaries.py` (added in phase 6) |
| **I5** — `shared` imports nothing from `cortex` at runtime | `shared` imports `config` (or any other group) and `layers` fails | `pyproject.toml` `layers` contract (shared is the bottom group) |


## Model

Model skipped — this feature adds no entity, no state and no flow: it relocates
type definitions (`MemoryContext`, `LoopEvent`, `Goal`) and adds two CI
contracts. The layers themselves are the diagram, and they live in
`docs/specs/architecture.md`; there is no lifecycle to draw.
