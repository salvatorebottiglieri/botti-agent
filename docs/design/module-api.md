# Program Design: module-api — the door, the sweep, the contract, the document

> `docs/specs/module-api.md` is the contract; this file is the shape.
> Tickets #189 → #192 (umbrella #187). Design written before the code, 2026-11-24.
> The machine part (file tree, signature) is in the `json design` block at the end.

## Shape of the change

Four tickets, one shape: **move where a name is imported from**, then make that
move a rule. No function body changes behaviour, no table, no route, no event:
the only callable whose shape changes is `ExecutionModule.__init__`, which stops
being able to invent a goal repository.

That is why the design declares no contract: a contract enumerates the exits of
a *call*, and this change adds none. What it changes is the **import surface**.

## What each door publishes

| Module | `__init__.py` today | After |
|---|---|---|
| `memory` | `ContextProvider`, `FactStore`, `Concept`, `ConfidenceLevel`, `Fact`, `FactMutability`, `FactType` | plus `SUPPORTED_EVENT_TYPES` (the minion gateway's vocabulary; `FactExtractor` stays on the open `interfaces`) |
| `trace` | `TraceRepository`, `TraceEvent`, `PostgresTraceRepository` | unchanged |
| `tools` | 30 names (interfaces, registry, executor, meta tools) | plus `AskUserTool`, `ASK_USER_CONTROL`, `ASK_USER_TOOL_NAME` |
| `rules` | docstring only: "Import the submodules (`cortex.rules.service`, …)" | `RuleService`, `render`, `enqueue_turn` + `interfaces`, `models` stay importable directly |
| `goals` | docstring only | `GoalRepository`, `InMemoryGoalRepository`, `PostgresGoalRepository` + `models` |

Two rules for every door, both structural:

1. **Only what a consumer needs.** A door that re-exports everything becomes a
   second implementation to maintain.
2. **Never a name that imports upward.** The package `__init__` runs on *every*
   submodule import (measured: `import cortex.memory.models` loads
   `context_provider`, `fact_store`, `evidence_store`, `db`, `asyncpg`), so a
   re-export that reaches a group above is an upward edge nobody asked for, and
   the `layers` contract would go red for it.

`interfaces` and `models` stay open: they are vocabulary, not implementation.

## The sweep (#190)

Ten runtime imports move, all downward, all to a name their door will publish:

| File | Today | After |
|---|---|---|
| `agentic/loop.py` | `from cortex.tools.meta.ask_user import ASK_USER_CONTROL, ASK_USER_TOOL_NAME` | `from cortex.tools import ASK_USER_CONTROL, ASK_USER_TOOL_NAME` |
| `agentic/reasoner.py` | `from cortex.rules.render import …` | `from cortex.rules import …` |
| `api/routes/chat.py` | `from cortex.rules.queue import enqueue_turn` | `from cortex.rules import enqueue_turn` |
| `eval/runner.py` | `cortex.tools.executor`, `cortex.tools.registry`, `memory.context_provider` | `cortex.tools`, `cortex.memory` |
| `eval/sandbox.py` | `cortex.tools.meta` (five tool classes) | `cortex.tools`, which gained `AskUserTool` for it |
| `execution/module.py` | `cortex.goals.repository`, constructed by default | `cortex.goals`, injected |
| `services/minion_service.py` | `memory.fact_extractor`, `memory.fact_store` | `cortex.memory` (`FactStore`, `SUPPORTED_EVENT_TYPES`); `FactExtractor` stays on the open `interfaces` |

`main.py` and `cli.py` keep importing concretes: the composition root builds
them, which is its job.

## The one signature that changes

`ExecutionModule` receives `goal_repository` as a **required** parameter, and
because Python forbids a required parameter after a defaulted one, it moves
next to `agent_loop`:

```diff
- def __init__(self, agent_loop, event_bus=None, goal_repository=None,
-              trace_recorder=None, conversation_extractor=None)
+ def __init__(self, agent_loop, goal_repository, event_bus=None,
+              trace_recorder=None, conversation_extractor=None)
```

Callers that already pass it by keyword (`main.py`) do not change. The four
tests that leaned on the default pass an in-memory repository instead.
`trace_recorder` and `conversation_extractor` keep their `None` default: `None`
is a meaningful state for them ("capture disabled"), while for the goal
repository it was a silent fallback to another module's implementation.

## The contracts (#191)

One `protected` contract per module, in the shape `rules` already uses, plus one
`forbidden` for the shared kernel. Written as configuration, so this is the
whole shape:

```toml
[[tool.importlinter.contracts]]
name = "memory internals are private to memory"
type = "protected"
protected_modules = ["cortex.memory.context_provider", "cortex.memory.fact_store",
                     "cortex.memory.fact_extractor", "cortex.memory.conversation_extractor",
                     "cortex.memory.evidence_store", "cortex.memory.repository"]
allowed_importers = ["cortex.memory", "cortex.main", "cortex.cli"]

[[tool.importlinter.contracts]]
name = "shared kernel imports no module"
type = "forbidden"
source_modules = ["cortex.shared"]
forbidden_modules = ["cortex.agentic", "cortex.api", "cortex.db", "cortex.eval",
                     "cortex.events", "cortex.execution", "cortex.goals",
                     "cortex.interaction", "cortex.learning", "cortex.llm",
                     "cortex.logging", "cortex.memory", "cortex.minions",
                     "cortex.rules", "cortex.safety", "cortex.services",
                     "cortex.sessions", "cortex.tools", "cortex.trace",
                     "cortex.validation"]
```

Each one is proven red on a control violation before it is kept — a scratch
import of a protected submodule from another module — and the scratch change is
discarded, the recipe ADR-0022 used. `tests` stay outside `root_package`, so the
76 imports from tests into internals keep working (Law 7 deferred, spec
`## Out of Scope`).

## Decision — the door is the package `__init__.py`

- **Choice**: a module's public surface is its package `__init__.py`;
  `interfaces` and `models` stay importable directly; every other submodule is
  protected by one contract per module, with the module itself, `cortex.main`
  and `cortex.cli` as the only allowed importers.
- **Alternatives rejected**: (B) a `facade.py` / `api.py` per module — a second
  file to keep in sync with `__init__.py`, and the layout rule becomes "one
  more file", not "the package is the surface"; (C) one wildcard contract
  (`protected_modules = ["cortex.*.service"]`, `allowed_importers =
  ["cortex.*"]`) — every importer matches the wildcard, so every module keeps
  entering every other's internals: a green check that checks nothing; (D)
  `root_packages = ["cortex", "tests"]` so the contracts also cover the 76 test
  imports — a second migration on top of this one, with no defect behind it.
- **Consequences**: a new module is born private (add its contract and one line
  in the architecture document); `import cortex.memory.models` keeps its current
  cost; a re-export that reaches upward fails the `layers` contract instead of
  silently closing a cycle; tests keep faking the seam they replace.
- **Owner sign-off**: 2026-11-24, owner in chat — "Mi torna il tuo consiglio,
  per quella roba la risolviamo con un ticket a valle" (the four choices: door
  in `__init__`, `models`/`interfaces` open, the five modules in scope, the
  repository required).

## Premises

- **Premise**: the doors that exist are unused, so the sweep changes imports only.
  - verdict: confirmed
  - evidence: `grep -rn "^from cortex\.\(memory\|rules\|tools\|goals\|trace\) import" src/ tests/` → two hits, both submodules (spec P4).
- **Premise**: using a door costs no extra import edge, because the package body already runs.
  - verdict: confirmed
  - evidence: `uv run python -c "import sys, cortex.memory.context_provider; print('cortex.memory' in sys.modules)"` → `True` (spec P5).
- **Premise**: `import-linter` accepts several protected modules per contract and package expressions as importers.
  - verdict: confirmed
  - evidence: installed source, `protected_modules = fields.ListField(subfield=fields.ModuleExpressionField())`; a two-module contract kept in a throwaway config (spec P7).

## External fixtures

None: no test stands in for a system the repo does not own.

```json design
{
  "feature": "module-api",
  "call_stack": {
    "name": "import cortex.<module>",
    "children": [
      { "name": "__init__", "note": "#189 — the package body re-exports the door names", "children": [] },
      { "name": "consumer", "note": "#190 — resolves the name through the door instead of the submodule", "children": [] }
    ]
  },
  "file_tree": [
    { "kind": "mod", "path": "src/cortex/goals/__init__.py", "note": "#189 the door: GoalRepository, InMemoryGoalRepository, PostgresGoalRepository" },
    { "kind": "mod", "path": "src/cortex/rules/__init__.py", "note": "#189 the door: RuleService, render, enqueue_turn; the 'import the submodules' docstring goes" },
    { "kind": "mod", "path": "src/cortex/tools/__init__.py", "note": "#189 adds ASK_USER_TOOL_NAME to __all__" },
    { "kind": "add", "path": "tests/unit/test_module_doors.py", "note": "#189 each door's __all__ names resolve; importing a submodule still loads the package" },
    { "kind": "mod", "path": "src/cortex/agentic/loop.py", "note": "#190 ASK_USER_TOOL_NAME from the tools door" },
    { "kind": "mod", "path": "src/cortex/agentic/reasoner.py", "note": "#190 render from the rules door" },
    { "kind": "mod", "path": "src/cortex/api/routes/chat.py", "note": "#190 enqueue_turn from the rules door" },
    { "kind": "mod", "path": "src/cortex/eval/runner.py", "note": "#190 tools and memory doors (three sites, one function-local)" },
    { "kind": "mod", "path": "src/cortex/eval/sandbox.py", "note": "#190 tools door" },
    { "kind": "mod", "path": "src/cortex/execution/module.py", "note": "#190 goal repository injected, never constructed; the goal types come from the goals door" },
    { "kind": "mod", "path": "src/cortex/services/minion_service.py", "note": "#190 fact extractor and fact store from the memory door" },
    { "kind": "mod", "path": "src/cortex/main.py", "note": "#190 the root already injects the goal repository; no change unless the parameter moves" },
    { "kind": "mod", "path": "tests/unit/execution/test_module.py", "note": "#190 three constructions gain an in-memory repository" },
    { "kind": "mod", "path": "tests/unit/api/test_chat_stream.py", "note": "#190 one construction gains an in-memory repository" },
    { "kind": "mod", "path": "pyproject.toml", "note": "#191 five protected contracts + the shared-kernel forbidden contract" },
    { "kind": "mod", "path": "tests/unit/test_module_boundaries.py", "note": "#191 asserts the six contracts exist and carry no ignore_imports" },
    { "kind": "mod", "path": "docs/specs/architecture.md", "note": "#192 the published interface per module, next to the layer order" },
    { "kind": "add", "path": "docs/adr/0023-module-published-interfaces.md", "note": "#192 the decision and its rejected alternatives" },
    { "kind": "mod", "path": "CONTEXT.md", "note": "#192 the term 'Published interface'" }
  ],
  "signatures": [
    {
      "module": "src/cortex/execution/module.py",
      "language": "python",
      "code": "def __init__(self, agent_loop: AgentLoop, goal_repository: GoalRepository, event_bus: EventBus | None = None, trace_recorder: TraceRecorder | None = None, conversation_extractor: ConversationFactExtractor | None = None) -> None: ..."
    }
  ],
  "data_flow": { "nodes": [], "edges": [] },
  "contracts": []
}
```
