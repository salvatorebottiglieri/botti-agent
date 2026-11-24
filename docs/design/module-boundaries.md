# Program Design: module-boundaries — the shared seam and the enforced layer order

> `docs/specs/module-boundaries.md` is the contract; this file is the shape.
> Issues #181–#184. Design written before the code, 2026-11-24.
> Signatures, the file-tree diff and the call graph are in the `json design` block.

## Shape of the change

This is a move of type definitions plus two CI contracts. No function body
changes behaviour: the dataclasses keep their fields, the bundle keeps its
dimensions, the rule system keeps its constants. The shape is therefore about
**where the definitions live** and **which import edges remain**.

Three groups move:

1. **The bundle** — `MemoryContext`, `PersonalityContext`, `AmbientContext`
   leave `agentic/models.py` for `shared/bundle.py`.
2. **The loop events** — `LoopEvent` and its seven subclasses leave
   `agentic/events.py` for `shared/loop_events.py`; `agentic/events.py` is
   removed rather than kept as a shim, so the old edge cannot survive.
3. **The goal model** — `Goal`, `GoalStep`, `GoalStatus` leave
   `agentic/models.py` for `goals/models.py`.

`agentic` keeps the loop, the reasoner, the executor, `Decision`, `Context`,
`ChatResponse`, `GoalResult`, `MaxIterationsError`: what the loop *does*, not
what the system *speaks*.

## Import edges after the move

| From | To | Kind |
|---|---|---|
| `memory` | `shared` | runtime (the bundle) |
| `trace`, `eval`, `api` | `shared` | runtime (the events) |
| `agentic` | `shared` | runtime (both) |
| `agentic` | `memory` | runtime (the bundle seam) — the only edge between the two |
| `goals`, `execution` | `goals` (own module) | — |
| `execution` | `goals` | runtime (`Goal`, `GoalStatus`) |

`memory → agentic` disappears; `trace → agentic` and `api → agentic` (for the
events) disappear; `goals → agentic` disappears. `agentic` imports neither
`goals` nor the event types it used to own: `run_goal` takes a description and
returns a `GoalResult`.

## Decision 1 — the shared contracts live below every module

- **Choice**: `cortex/shared/` holds `bundle.py` (the three context types) and
  `loop_events.py` (`LoopEvent` + seven subclasses); it imports nothing from
  `cortex` at runtime (only `TYPE_CHECKING` annotations for `Fact`, `Rule`,
  `UsageStats`).
- **Alternatives rejected**: (B) no shared module — `MemoryContext` in
  `memory`, the events in a new `cortex/loop_protocol`, `Goal` in `goals`:
  three ownerships for one contract, and `agentic` still imports `memory` for
  the bundle it renders; (C) leave the types in `agentic` and add
  `ignore_imports` for `memory → agentic`: the cycle stays, the check lies.
- **Consequences**: one new module at the bottom of the scale; `agentic` keeps
  the behaviour and loses the vocabulary; the bundle and the event stream are
  importable without importing the loop. A future shared type has one obvious
  home and one rule to respect (no runtime `cortex` imports).
- **Owner sign-off**: 2026-11-24, owner in chat — "piano ok, A" (A = a
  `cortex/shared/` module for the bundle and loop-event types, `Goal` in
  `goals`).

## Decision 2 — `eval` and `trace` sit above `agentic` in the layer scale

- **Choice**: the layer order is `integration → orchestration → consumers
  (eval, trace) → agentic → services → modules → primitives → foundation →
  shared`; a higher group may import a lower one.
- **Alternatives rejected**: (B) keep `eval` in the module layer and add
  `ignore_imports` for `eval → agentic` — the harness drives the real loop by
  design, so the exception would be permanent and the layer scale would be
  fiction; (C) inject a loop factory into eval so the edge becomes a port —
  a seam for a test harness that exists to exercise the real loop, and it
  would hide the dependency this feature exists to make visible.
- **Consequences**: the architecture diagram and the numeric layering are
  rewritten to say the same thing (`Eval --> Loop` was already drawn, the
  numbers said otherwise); `trace` rides the same layer because it consumes
  the loop's event stream and the eval judge.
- **Owner sign-off**: 2026-11-24, owner in chat — "piano ok, A" (the plan
  shown placed `eval` above `agentic`).

## Decision 3 — type-only imports are not edges (global exclusion)

- **Choice**: `[tool.importlinter]` sets `exclude_type_checking_imports = true`
  once, for every contract. A `TYPE_CHECKING` annotation alias is not a
  runtime edge and cannot close a cycle.
- **Alternatives rejected**: (B) count them — then `config → llm` (a single
  `UsageStats` annotation) forbids a legitimate direction, and the fix would
  be moving a settings type for no runtime reason; (C) per-contract options —
  measured: the option is a session option, not a contract one.
- **Consequences**: the contracts describe the runtime graph; a type-only
  upward import passes, and the negative control in #183 proves it does. A
  runtime import of the same module still fails.
- **Owner sign-off**: 2026-11-24, owner in chat — "piano ok, A" (the plan
  shown included the global `exclude_type_checking_imports`).

## Exits and test list (the implementer's contract)

- `tests/unit/test_module_boundaries.py::test_whole_map_contracts_carry_no_exception_list`
  — parses `pyproject.toml`, asserts `layers` and `acyclic_siblings` exist and
  neither carries `ignore_imports` (invariant I4).
- `tests/unit/test_module_boundaries.py::test_shared_imports_nothing_from_cortex`
  — AST-scans `src/cortex/shared/` and asserts no runtime `cortex` import
  (invariant I5). A `TYPE_CHECKING` import is allowed and asserted allowed.
- Existing suites: full `pytest`, `ruff`, `mypy`, `lint-imports` (the two
  protected contracts plus the two new ones), `check_design --gate`,
  `check_model --gate --strict`.
- The two control violations for #183 (an upward import; a two-module cycle)
  are executed and reverted by the implementer and quoted in the PR body.

## External fixtures

None. The feature touches no external system and adds no fixture.

## JSON design

```json design
{
 "feature": "module-boundaries",
 "file_tree": [
  {
   "kind": "add",
   "path": "src/cortex/shared/__init__.py",
   "note": "#181 the package marker; no re-exports, so an import names the module it needs"
  },
  {
   "kind": "add",
   "path": "src/cortex/shared/bundle.py",
   "note": "#181 PersonalityContext, AmbientContext, MemoryContext; Fact/Rule under TYPE_CHECKING only"
  },
  {
   "kind": "add",
   "path": "src/cortex/shared/loop_events.py",
   "note": "#181 LoopEvent + seven subclasses, exact shapes carried over from agentic/events.py"
  },
  {
   "kind": "remove",
   "path": "src/cortex/agentic/events.py",
   "note": "#181 deleted, not kept as a shim: the old import edge must not survive"
  },
  {
   "kind": "mod",
   "path": "src/cortex/agentic/models.py",
   "note": "#181/#182 loses the three context types and Goal/GoalStep/GoalStatus; keeps Decision, Context, ChatResponse, GoalResult, MaxIterationsError"
  },
  {
   "kind": "add",
   "path": "src/cortex/goals/models.py",
   "note": "#182 GoalStatus, GoalStep, Goal"
  },
  {
   "kind": "mod",
   "path": "src/cortex/memory/context_provider.py",
   "note": "#181 imports the bundle from cortex.shared.bundle"
  },
  {
   "kind": "mod",
   "path": "src/cortex/trace/recorder.py",
   "note": "#181 imports the events from cortex.shared.loop_events"
  },
  {
   "kind": "mod",
   "path": "src/cortex/trace/audit.py",
   "note": "#181 same, plus the judge stays eval"
  },
  {
   "kind": "mod",
   "path": "src/cortex/eval/runner.py",
   "note": "#181 events from shared; the function-local MemoryContext at :172 from shared too"
  },
  {
   "kind": "mod",
   "path": "src/cortex/eval/metrics.py",
   "note": "#181 imports the events from cortex.shared.loop_events (found during implementation; the first file-tree missed it)"
  },
  {
   "kind": "mod",
   "path": "src/cortex/eval/judge.py",
   "note": "#181 same (found during implementation)"
  },
  {
   "kind": "mod",
   "path": "src/cortex/api/routes/chat.py",
   "note": "#181 LoopEvent from shared (runtime importer, spec premise P7)"
  },
  {
   "kind": "mod",
   "path": "src/cortex/execution/module.py",
   "note": "#182 Goal/GoalStatus from goals; the TYPE_CHECKING LoopEvent from shared"
  },
  {
   "kind": "mod",
   "path": "src/cortex/goals/interfaces.py",
   "note": "#182 Goal from goals.models"
  },
  {
   "kind": "mod",
   "path": "src/cortex/goals/repository.py",
   "note": "#182 Goal/GoalStatus from goals.models"
  },
  {
   "kind": "mod",
   "path": "src/cortex/agentic/loop.py",
   "note": "#181 events from shared; Goal from goals"
  },
  {
   "kind": "add",
   "path": "tests/unit/test_module_boundaries.py",
   "note": "#183 I4 (no exception list) and I5 (shared imports nothing from cortex at runtime)"
  },
  {
   "kind": "mod",
   "path": "pyproject.toml",
   "note": "#183 global exclude_type_checking_imports; layers and acyclic_siblings contracts; the stale two-cycle comment corrected"
  },
  {
   "kind": "mod",
   "path": "docs/specs/architecture.md",
   "note": "#184 the enforced order, the shared layer, eval/trace above agentic"
  },
  {
   "kind": "add",
   "path": "docs/adr/0022-module-layers-and-shared-seam.md",
   "note": "#184 the decision and its rejected alternatives"
  }
 ],
 "signatures": [
  {
   "module": "src/cortex/shared/bundle.py",
   "language": "python",
   "code": "from __future__ import annotations\n\nfrom dataclasses import dataclass, field\nfrom typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from cortex.memory.models import Fact\n    from cortex.rules.models import Rule\n\n\n@dataclass\nclass PersonalityContext:\n    formality: float = 0.5\n    verbosity: float = 0.5\n    technical_level: float = 0.5\n    humor_level: float = 0.5\n    empathy: float = 0.5\n    directness: float = 0.5\n\n\n@dataclass\nclass AmbientContext:\n    time_of_day: str | None = None\n    day_of_week: str | None = None\n    location: str | None = None\n    activity: str | None = None\n    weather: str | None = None\n\n\n@dataclass\nclass MemoryContext:\n    facts: list[Fact] = field(default_factory=list)\n    personality: PersonalityContext | None = None\n    ambient: AmbientContext | None = None\n    rules: list[Rule] = field(default_factory=list)\n    degraded_dimensions: list[str] = field(default_factory=list)"
  },
  {
   "module": "src/cortex/shared/loop_events.py",
   "language": "python",
   "code": "from __future__ import annotations\n\nfrom dataclasses import asdict, dataclass, field\nfrom typing import TYPE_CHECKING, Any, ClassVar\nfrom uuid import UUID\n\nif TYPE_CHECKING:\n    from cortex.llm.models import UsageStats\n\n\n@dataclass\nclass LoopEvent:\n    event_type: ClassVar[str] = \"\"\n    session_id: UUID\n\n    def to_dict(self) -> dict[str, Any]: ...\n\n\n@dataclass\nclass ThinkingEvent(LoopEvent):\n    event_type: ClassVar[str] = \"thinking\"\n    message: str\n\n\n@dataclass\nclass TextDeltaEvent(LoopEvent):\n    event_type: ClassVar[str] = \"text\"\n    delta: str\n\n\n@dataclass\nclass ToolStartEvent(LoopEvent):\n    event_type: ClassVar[str] = \"tool_start\"\n    tool_name: str\n    tool_call_id: str\n\n\n@dataclass\nclass ToolResultEvent(LoopEvent):\n    event_type: ClassVar[str] = \"tool_done\"\n    tool_name: str\n    tool_call_id: str\n    success: bool\n    output: str | None = None\n    error: str | None = None\n    execution_time_ms: float | None = None\n\n\n@dataclass\nclass ResponseDoneEvent(LoopEvent):\n    event_type: ClassVar[str] = \"done\"\n    message: str\n    tools_used: list[str] = field(default_factory=list)\n    iterations: int = 0\n    usage: UsageStats | None = None\n    latency_ms: float | None = None\n    notice: str | None = None\n\n    def to_dict(self) -> dict[str, Any]: ...\n\n\n@dataclass\nclass AskUserEvent(LoopEvent):\n    event_type: ClassVar[str] = \"ask_user\"\n    question: str\n    options: list[str] = field(default_factory=list)\n\n\n@dataclass\nclass ErrorEvent(LoopEvent):\n    event_type: ClassVar[str] = \"error\"\n    error: str\n    code: str | None = None"
  },
  {
   "module": "src/cortex/goals/models.py",
   "language": "python",
   "code": "from __future__ import annotations\n\nfrom dataclasses import dataclass, field\nfrom enum import Enum\nfrom typing import Any\nfrom uuid import UUID, uuid4\n\n\nclass GoalStatus(Enum):\n    PENDING = \"pending\"\n    RUNNING = \"running\"\n    PAUSED = \"paused\"\n    COMPLETED = \"completed\"\n    FAILED = \"failed\"\n\n\n@dataclass\nclass GoalStep:\n    step_number: int\n    action: str\n    result: str | None = None\n    error: str | None = None\n    started_at: Any | None = None\n    completed_at: Any | None = None\n\n\n@dataclass\nclass Goal:\n    id: UUID = field(default_factory=uuid4)\n    description: str = \"\"\n    status: GoalStatus = GoalStatus.PENDING\n    priority: str = \"normal\"\n    steps: list[GoalStep] = field(default_factory=list)\n    created_at: Any | None = None\n    started_at: Any | None = None\n    completed_at: Any | None = None\n    deadline: Any | None = None\n    error: str | None = None"
  },
  {
   "module": "tests/unit/test_module_boundaries.py",
   "language": "python",
   "code": "def test_whole_map_contracts_carry_no_exception_list() -> None: ...\n\ndef test_shared_imports_nothing_from_cortex() -> None: ..."
  }
 ]
}
```
