# Program Design — concept events + validated storage (#29)

## Context

#27 added the symbolic validator; #28 added the LLM revision loop. #29 closes
ADR-0007: emit `concept.validated` / `concept.rejected`, and store only
validated concepts.

The ticket names `MemoryService.store_concept()`, but `MemoryService` was
deleted in #21 (`9a0357e`). The current concept path is
`ContextProvider.build_concept()` (`src/cortex/memory/context_provider.py`),
which stores every concept with `validated=False` and has no validation step.
`build_concept`'s `proof_chain` argument is a **natural-language** string
built from facts, not a Datalog proof, so it cannot feed the validator
unchanged.

## Decisions

- **The validated entry point is a new `ContextProvider.store_concept(derivation: ConceptDerivation)`.**
  `build_concept` stays the legacy/manual builder (it cannot produce Datalog
  clauses); callers that have a proof chain use `store_concept`.
- **`store_concept` requires the derivation service.** `ContextProvider` still
  accepts `concept_derivation_service=None` so existing constructions keep
  working, but `store_concept` raises
  `RuntimeError("concept derivation service is not configured")` when it is
  absent. There is no unvalidated store path; `build_concept` remains the only
  legacy direct-store builder.
- **The derivation carries its own sources.** `store_concept` fetches each
  `source_fact_ids` entry through the `FactRepository`, keeps only active
  facts, and loads them into the validation engine. Concepts are stored only
  when the outcome is `valid`; rejected concepts are never written.
- **Events are emitted by `ConceptDerivationService`, not by `ContextProvider`.**
  The derivation service gains an optional `event_bus`; the memory layer does
  not know about event payloads. No bus → no emission.
- **Emission precedes storage.** `derive_and_validate` emits the terminal event
  before `store_concept` persists the concept, so `concept.validated` means
  "validation passed", not "row persisted". The event carries the pre-storage
  concept id, which may differ from the id the repository assigns on insert
  (follow-up below).
- **Event emission is best-effort.** A publish failure is logged and does not
  change the validation outcome; the events are observational, not a
  transactional part of storage.
- **Rejected events carry the last attempted concept.** `DerivationOutcome`
  has `concept=None` on rejection, but the event needs an id/symbolic repr;
  the service uses the last derivation it validated (original or last
  revision).
- **`load_facts` is exposed through the services.** `ConceptValidationService`
  delegates to its `LogicEngine`; `ConceptDerivationService` delegates to the
  validation service. `ContextProvider` calls the derivation service, so there
  is one engine instance per composition root.
- **`main.py` wires the pipeline** after the LLM client and event bus already
  exist: `LogicEngine` → `ConceptValidationService` → `ConceptDerivationService`
  → `ContextProvider(concept_derivation_service=...)`.

## Event payloads

`concept.validated` (emitted once per valid outcome, including first-try):

```json
{
  "concept_id": "<uuid>",
  "symbolic_repr": "...",
  "natural_lang_repr": "...",
  "confidence": 0.7,
  "source_facts": ["<uuid>", "..."],
  "attempts": 0,
  "timestamp": 1730000000.0
}
```

`concept.rejected` (emitted once, after the revision budget):

```json
{
  "concept_id": "<uuid of the last attempted concept>",
  "symbolic_repr": "...",
  "rejection_reason": "...",
  "source_facts": ["<uuid>", "..."],
  "attempts": 3,
  "timestamp": 1730000000.0
}
```

`BaseEvent.create(event_type, payload, source_module="validation")` is owned by
`EventEmitter(event_bus, source_module="validation")`.

> **Validation-scoped, not storage-scoped.** Both events are emitted inside
> `derive_and_validate`, before `ContextProvider.store_concept` calls
> `ConceptRepository.store`. `concept.validated` therefore reports that the
> concept passed validation; it does not prove the row was persisted. The
> `concept_id` in the payload is the pre-storage, in-memory concept id; the
> stored row may receive a different id. Aligning the event id with the stored
> id is tracked as a follow-up (the parent opens the issue).

## Call-stack tree

```diff
 ConceptDerivationService.derive_and_validate           # MODIFIED — emit terminal events
+  _emitter.emit(EventTypes.CONCEPT_VALIDATED, payload) # valid outcome
+  _emitter.emit(EventTypes.CONCEPT_REJECTED, payload)  # after the budget
+    EventBus.publish(BaseEvent)                        # external side effect
 ConceptValidationService.load_facts                    # NEW — delegates to LogicEngine
 ContextProvider.store_concept                          # NEW — validated entry point
+  raise RuntimeError(...)                              # service absent: no unvalidated store path
+  FactRepository.get(fact_id)                          # active sources only
+  ConceptDerivationService.load_facts
+  ConceptDerivationService.derive_and_validate
+  ConceptRepository.store                              # only when valid
```

## File-tree diff

```diff
 src/cortex/validation
~  derivation_service.py   # MODIFIED — EventEmitter, terminal events, validate/store outcomes
~  service.py              # MODIFIED — load_facts delegation
 ~  __init__.py             # unchanged exports (DerivationOutcome already public)
 src/cortex/memory
~  context_provider.py     # MODIFIED — optional derivation service + store_concept
 src/cortex
~  main.py                 # MODIFIED — wire LogicEngine/validation/derivation into ContextProvider
 src/cortex/events
~  types.py                # MODIFIED — concept.validated/concept.rejected constants
 tests/validation
+  test_event_emission.py   # NEW — validated/rejected payloads, no bus, publish failure
 tests/memory
+  test_concept_validation.py   # NEW — store only valid, source loading, missing service raises
 tests/unit
~  test_events.py          # MODIFIED — EventTypes concept constants
 docs/design
+  concept-events.md        # this design
 docs/specs
~  architecture.md         # MODIFIED — module map, event table, composition, graph
```

## Type signatures

```python
# src/cortex/validation/service.py
class ConceptValidationService:
    def load_facts(self, facts: list[Fact]) -> None: ...


# src/cortex/validation/derivation_service.py
class ConceptDerivationService:
    def __init__(
        self,
        validation_service: ConceptValidationService,
        llm_client: LLMClient,
        event_bus: EventBus | None = None,
    ) -> None: ...

    def load_facts(self, facts: list[Fact]) -> None: ...

    async def derive_and_validate(
        self,
        proposed_derivation: ConceptDerivation,
        max_retries: int = 3,
    ) -> DerivationOutcome: ...


# src/cortex/memory/context_provider.py
class ContextProvider:
    def __init__(
        self,
        fact_repository: FactRepository,
        fact_store: FactStore,
        concept_repository: ConceptRepository | None = None,
        rule_service: RuleService | None = None,
        concept_derivation_service: ConceptDerivationService | None = None,
    ) -> None: ...

    async def store_concept(self, derivation: ConceptDerivation) -> Concept | None: ...
```

## Data flow

```
ContextProvider.store_concept
        ├── no derivation service ──► RuntimeError, nothing loaded or stored
        │  fetch active facts by id
        ▼
ConceptDerivationService.load_facts ──► ConceptValidationService.load_facts ──► LogicEngine.load_facts
        │
        ▼
ConceptDerivationService.derive_and_validate
        ├── valid ──► emit concept.validated ──► return valid outcome
        └── rejected ──► emit concept.rejected ──► return rejected outcome
        │
        ▼
ContextProvider.store_concept (after derive_and_validate returns)
        ├── valid ──► ConceptRepository.store(concept)
        └── rejected ──► return None, never stored
```

`concept.validated` means "validation passed", not "row persisted": the event is
already on the bus when storage runs, and the stored id may differ from the
event's pre-storage `concept_id` (follow-up).

## Contracts

| site | callee | returns | absent | raises | effect |
|---|---|---|---|---|---|
| `store_concept` | `self._concept_derivation_service` (own state) | `ConceptDerivationService` | `None` → raises before any fact lookup or store | `RuntimeError("concept derivation service is not configured")` | no facts loaded, nothing stored |
| `store_concept` | `derivation_service.derive_and_validate` | `DerivationOutcome` | never — always returns | none — the derivation service catches LLM/validation errors | stores only on valid |
| `store_concept` | `FactRepository.get` (external) | `Fact | None` | `None` = the fact was deleted, skipped | repository errors propagate (storage path) | reads |
| `derive_and_validate` | `EventEmitter.emit` (external bus) | `None` | never — a failure is logged | publish errors are caught and logged; they do not change the outcome | emits an event |

```json design
{
  "feature": "concept-events",
  "call_stack": {
    "name": "ContextProvider.store_concept",
    "children": [
      { "name": "FactRepository.get", "note": "one lookup per source fact id; active only" },
      { "name": "ConceptDerivationService.load_facts", "note": "delegates to the validation service" },
      { "name": "ConceptDerivationService.derive_and_validate", "note": "runs the #27/#28 pipeline" },
      { "name": "ConceptRepository.store", "note": "only on a valid outcome" }
    ]
  },
  "file_tree": [
    { "kind": "mod", "path": "src/cortex/validation/derivation_service.py", "note": "EventEmitter, event types, terminal events" },
    { "kind": "mod", "path": "src/cortex/validation/service.py", "note": "load_facts delegation" },
    { "kind": "mod", "path": "src/cortex/memory/context_provider.py", "note": "optional derivation service + store_concept" },
    { "kind": "mod", "path": "src/cortex/main.py", "note": "wire the validation pipeline" },
    { "kind": "mod", "path": "src/cortex/events/types.py", "note": "concept.validated/concept.rejected constants" },
    { "kind": "add", "path": "tests/validation/test_event_emission.py", "note": "validated/rejected payloads, no bus, publish failure" },
    { "kind": "add", "path": "tests/memory/test_concept_validation.py", "note": "store only valid, source loading, missing service raises" },
    { "kind": "mod", "path": "tests/unit/test_events.py", "note": "EventTypes concept constants" },
    { "kind": "add", "path": "docs/design/concept-events.md", "note": "this design" },
    { "kind": "mod", "path": "docs/specs/architecture.md", "note": "module map, event table, composition, graph" }
  ],
  "signatures": [
    {
      "module": "src/cortex/validation/service.py",
      "language": "python",
      "code": "class ConceptValidationService:\n    def load_facts(self, facts: list[Fact]) -> None: ..."
    },
    {
      "module": "src/cortex/validation/derivation_service.py",
      "language": "python",
      "code": "class ConceptDerivationService:\n    def __init__(\n        self,\n        validation_service: ConceptValidationService,\n        llm_client: LLMClient,\n        event_bus: EventBus | None = None,\n    ) -> None: ...\n\n    def load_facts(self, facts: list[Fact]) -> None: ...\n\n    async def derive_and_validate(\n        self,\n        proposed_derivation: ConceptDerivation,\n        max_retries: int = 3,\n    ) -> DerivationOutcome: ..."
    },
    {
      "module": "src/cortex/memory/context_provider.py",
      "language": "python",
      "code": "class ContextProvider:\n    def __init__(\n        self,\n        fact_repository: FactRepository,\n        fact_store: FactStore,\n        concept_repository: ConceptRepository | None = None,\n        rule_service: RuleService | None = None,\n        concept_derivation_service: ConceptDerivationService | None = None,\n    ) -> None: ...\n\n    async def store_concept(self, derivation: ConceptDerivation) -> Concept | None: ..."
    }
  ],
  "data_flow": {
    "nodes": [
      { "id": "context_provider.store_concept", "module": "src/cortex/memory/context_provider.py", "kind": "add" },
      { "id": "derivation_service.derive_and_validate", "module": "src/cortex/validation/derivation_service.py", "kind": "mod" },
      { "id": "validation_service.load_facts", "module": "src/cortex/validation/service.py", "kind": "add" }
    ],
    "edges": [
      { "from": "context_provider.store_concept", "to": "derivation_service.derive_and_validate" },
      { "from": "context_provider.store_concept", "to": "validation_service.load_facts" }
    ]
  },
  "contracts": [
    {
      "site": "context_provider.store_concept",
      "call": "raise RuntimeError(...) when self._concept_derivation_service is None",
      "residual": "the guard runs before any FactRepository.get or ConceptRepository.store; an unconfigured provider cannot store an unvalidated concept",
      "exits": [
        { "kind": "returns", "type": "ConceptDerivationService", "handled_at": "src/cortex/memory/context_provider.py:store_concept" },
        { "kind": "absent", "type": "None", "handled_at": "src/cortex/memory/context_provider.py:store_concept raises RuntimeError before any lookup or store" },
        { "kind": "raises", "type": "RuntimeError", "handled_at": "src/cortex/memory/context_provider.py:store_concept" },
        { "kind": "effect", "type": "nothing loaded or stored", "handled_at": null }
      ]
    },
    {
      "site": "context_provider.store_concept",
      "callee": "derivation_service.derive_and_validate",
      "exits": [
        { "kind": "returns", "type": "DerivationOutcome", "handled_at": "src/cortex/memory/context_provider.py:store_concept" },
        { "kind": "absent", "type": "never", "handled_at": "the derivation service always returns an outcome" },
        { "kind": "raises", "type": "none", "handled_at": "the derivation service catches LLM/validation errors" },
        { "kind": "effect", "type": "stores only a valid concept", "handled_at": null }
      ]
    },
    {
      "site": "context_provider.store_concept",
      "callee": "validation_service.load_facts",
      "exits": [
        { "kind": "returns", "type": "None", "handled_at": "src/cortex/memory/context_provider.py:store_concept" },
        { "kind": "absent", "type": "never", "handled_at": "always loads the active source facts" },
        { "kind": "raises", "type": "none", "handled_at": "LogicEngine.load_facts cannot raise on sanitised facts" },
        { "kind": "effect", "type": "replaces the engine's axiom set", "handled_at": null }
      ]
    },
    {
      "site": "derivation_service.derive_and_validate",
      "call": "await self._emitter.emit(...) — EventEmitter over the external event bus",
      "residual": "publish failures are caught and logged; the validation outcome is unchanged, and no bus means no emission",
      "exits": [
        { "kind": "returns", "type": "None", "handled_at": "src/cortex/validation/derivation_service.py:_emitter.emit" },
        { "kind": "absent", "type": "never", "handled_at": "a bus either publishes or raises" },
        { "kind": "raises", "type": "Exception", "handled_at": "caught in EventEmitter.emit and logged; the outcome is unchanged" },
        { "kind": "effect", "type": "emits concept.validated or concept.rejected", "handled_at": null }
      ]
    }
  ]
}
```

## Premises

- **Premise**: `MemoryService.store_concept` is the storage seam.
  - verdict: falsified
  - evidence: `MemoryService` was deleted in #21 (`9a0357e`); the current path is
    `ContextProvider.build_concept` (`src/cortex/memory/context_provider.py:243`).
- **Premise**: `ContextProvider.build_concept` can feed the validator directly.
  - verdict: falsified
  - evidence: its `proof_chain` is a natural-language string built by
    `_build_proof_chain`, not Datalog clauses; the new `store_concept` takes a
    `ConceptDerivation` with a list of clauses.
- **Premise**: `FactRepository.get` returns the source facts by id and retracted
  facts can be filtered with `is_active()`.
  - verdict: confirmed
  - evidence: `src/cortex/memory/interfaces.py` (`async def get`); `Fact.is_active`.
- **Premise**: `EventBus.publish` is async and raises when the bus is not running.
  - verdict: confirmed
  - evidence: `src/cortex/events/bus.py`; `EventBusError` on a stopped bus.
- **Premise**: a rejected outcome has no concept to put in the event payload.
  - verdict: confirmed
  - evidence: #28 `DerivationOutcome(concept=None)` on rejection; the service
    keeps the last attempted derivation for the payload.
- **Premise**: the composition root can build the pipeline after the LLM client
  and event bus exist.
  - verdict: confirmed
  - evidence: `src/cortex/main.py` wiring order (event bus at 4, LLM at 5d,
    `ContextProvider` at 6).
