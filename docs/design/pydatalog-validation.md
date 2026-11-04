# Program Design — PyDatalog concept validation (#27)

## Context

ADR-0007 introduces a symbolic validation step for LLM-derived concepts:
`LogicEngine` loads the source facts as Datalog axioms, parses the proposed
proof chain, and returns a `ValidationResult`. This ticket is the pure-symbolic
core only — no LLM retry loop (#28), no event emission or `MemoryService`
wiring (#29).

The current code has no `src/cortex/validation/` package, no
`ConceptDerivation`, and no `LogicEngine`. `Concept` already has `validated`,
`proof_chain` and `source_facts` fields, and the `concepts` table already has a
`validated` column (ADR-0007: no migration).

## Falsified premises (measured, not assumed)

1. **PyDatalog works on Python 3.12.** Confirmed:
   `uv run --no-project --with pydatalog python -c "import pyDatalog"` imports
   `pydatalog==0.22.4` (one `SyntaxWarning` from the package itself).
2. **Plain Datalog detects "same predicate, different value".** **Falsified.**
   `location('user','home')` plus `location('user','office')` is satisfiable in
   Datalog; a conflict only appears with an explicit functional-predicate rule
   `location(X,P1) & location(X,P2) & (P1 != P2)`. The engine therefore owns a
   `FUNCTIONAL_PREDICATES` registry (`location`, `activity` for now) and checks
   it structurally.
3. **The ticket's `location(fact_id, place)` encoding detects the documented
   example.** **Falsified.** With the fact id as the subject, two facts have
   different subjects, so a conflict rule over the subject cannot fire
   (`conflict` returned `None` in the prototype). Facts are encoded as
   `predicate('user', 'value')` — the single-user model — and the fact id is
   kept in a Python `value -> Fact` index for `conflicting_facts`.
4. **`pyDatalog.clear()` returns `None` for an undefined predicate.**
   **Falsified.** It raises
   `AttributeError: Predicate without definition (or error in resolver)`.
   `ask` is wrapped and treats that as "no answer".
5. **`proof_chain` can be passed straight to PyDatalog.** **Falsified as
   unsafe.** `pyParser.load` parses with `ast.parse`, compiles and `exec`s the
   result. LLM output is untrusted, so every clause is validated by a strict
   Datalog grammar (predicates, variables, quoted/bare constants, `<=`/`:-`,
   `&`) before it reaches PyDatalog. Anything else is a `ValidationResult`,
   never an exception.
6. **`MemoryService` exists at `src/cortex/services/memory_service.py`.** The
   research note says so; it was deleted in #21 (`9a0357e`). The insertion point
   is now `ContextProvider.build_concept`, and wiring is #29. #27 does not
   touch it.
7. **Existing `symbolic_repr` is Datalog syntax.** It is dot-separated
   (`location.home`, `activity.working`, see `FactExtractor`). `_fact_atom`
   accepts both the dot form (value = suffix) and a future
   `predicate(arg, arg)` form (value = last ground arg); payload keys
   (`place`, `activity`, `value`) are the fallback.

## Decisions

- **`predicate('user', 'value')` facts.** Stable subject, value in the second
  argument, fact provenance in a Python index. Functional predicates with two
  coexisting values are a contradiction by construction.
- **The proof chain's last clause is the conclusion.** It must be a *ground*
  atom. The preceding clauses are rules (and optionally facts); `:-` is
  normalised to PyDatalog's `<=`. This is the minimal convention that makes
  "does the conclusion follow?" a single `ask`.
- **Conflict model.** For each functional predicate with loaded facts:
  - more than one distinct fact value for a functional predicate is a cross-fact
    contradiction; every loaded fact for that predicate is conflicting;
  - a single fact value conflicts only when the proof grounds a different value.
  Non-functional predicates may legitimately have several values (two payments,
  two preferences) and never conflict.
- **`load_facts` replaces the loaded set.** It stores the active facts and
  resets the process-global PyDatalog engine. `validate_derivation` re-asserts
  that stored set before every run, so repeated validations are idempotent and
  one engine's facts do not leak into another's result.
- **The PyDatalog engine is process-global.** A module-level `RLock` serialises
  `load_facts`/`validate_derivation`; the service is synchronous by contract.
- **No `main.py` wiring, no LLM, no event bus.** #28 and #29 own those.

## Call-stack tree

```diff
 ConceptValidationService.validate                    # NEW — pure delegation seam
+  LogicEngine.validate_derivation
+    _ClauseParser.parse / _parse_proof (strict Datalog grammar, injection guard)
+    _required_values (ground body atoms on functional predicates)
+    _reset_engine
+      pyDatalog.clear
+      pyParser.load (facts)
+    pyParser.load (rules)
+    pyDatalog.ask (conclusion)
+    _find_conflicts (structural, over the fact index)
+  ValidationResult(valid, conflicting_facts, suggested_fix)
 LogicEngine.load_facts                               # NEW
+  pyDatalog.clear
+  pyParser.load (one atom per active fact)
```

## File-tree diff

```diff
 src/cortex
+  validation/
+    __init__.py        # exports LogicEngine, ConceptValidationService, ValidationResult
+    models.py          # NEW — ValidationResult dataclass
+    logic_engine.py    # NEW — strict parser + PyDatalog wrapper + conflict model
+    service.py         # NEW — ConceptValidationService
 ~  memory/models.py    # MODIFIED — ConceptDerivation dataclass
 tests
+  validation/
+    __init__.py
+    test_logic_engine.py   # NEW — valid/contradicting/unsupported/malformed/security
+    test_service.py        # NEW — delegation contract
 pyproject.toml         # MODIFIED — pydatalog dependency
 uv.lock                # MODIFIED — resolved lock
 .github/workflows/ci.yml  # MODIFIED — run tests/validation in the test and coverage jobs
```

## Type signatures

```python
# src/cortex/memory/models.py
@dataclass
class ConceptDerivation:
    concept: Concept
    proof_chain: list[str]
    source_fact_ids: list[UUID] = field(default_factory=list)


# src/cortex/validation/models.py
@dataclass
class ValidationResult:
    valid: bool
    conflicting_facts: list[Fact] = field(default_factory=list)
    suggested_fix: str | None = None


# src/cortex/validation/logic_engine.py
FUNCTIONAL_PREDICATES: frozenset[str] = frozenset({"location", "activity"})

class LogicEngine:
    def __init__(self) -> None: ...
    def load_facts(self, facts: list[Fact]) -> None: ...
    def validate_derivation(self, derivation: ConceptDerivation) -> ValidationResult: ...


# src/cortex/validation/service.py
class ConceptValidationService:
    def __init__(self, logic_engine: LogicEngine) -> None: ...
    def validate(self, concept_derivation: ConceptDerivation) -> ValidationResult: ...
```

## Data flow

```
ConceptValidationService.validate
        │
        ▼
LogicEngine.validate_derivation ──► pyParser.load / pyDatalog.ask   (external)
        ▲
        │ stored active facts
LogicEngine.load_facts ───────────► pyDatalog.clear / pyParser.load (external)
```

## Contracts

| site | callee | returns | absent | raises | effect |
|---|---|---|---|---|---|
| `service.validate` | `logic_engine.validate_derivation` | `ValidationResult` | never — delegation always returns | none — engine catches parser/PyDatalog errors | none |
| `logic_engine.load_facts` | `pyParser.load(atom, newglobals={})` (external) | `None` | never — a fact either yields an atom or is skipped | `DatalogError` — residual: the atom is generated from sanitised values, never raw user text | resets the process-global engine |
| `logic_engine.validate_derivation` | `pyDatalog.ask` (external) | `Answer | None`; `ValidationResult` externally | `None` = not derivable, handled as invalid | `AttributeError` for an undefined predicate is caught and treated as no answer; other exceptions are caught and returned as invalid | mutates the global engine |

```json design
{
  "feature": "pydatalog-validation",
  "call_stack": {
    "name": "ConceptValidationService.validate",
    "children": [
      {
        "name": "LogicEngine.validate_derivation",
        "note": "parse the proof, reset the engine, load rules, ask the conclusion, find conflicts",
        "children": [
          { "name": "_ClauseParser.parse / _parse_proof", "note": "strict Datalog grammar; rejects anything PyDatalog would exec as Python" },
          { "name": "_required_values", "note": "ground body atoms on functional predicates" },
          { "name": "_reset_engine", "note": "clear + re-assert the stored active facts" },
          { "name": "_find_conflicts", "note": "structural over the fact index" }
        ]
      }
    ]
  },
  "file_tree": [
    { "kind": "add", "path": "src/cortex/validation/__init__.py", "note": "public exports" },
    { "kind": "add", "path": "src/cortex/validation/models.py", "note": "ValidationResult" },
    { "kind": "add", "path": "src/cortex/validation/logic_engine.py", "note": "parser, PyDatalog wrapper, conflict model" },
    { "kind": "add", "path": "src/cortex/validation/service.py", "note": "ConceptValidationService" },
    { "kind": "mod", "path": "src/cortex/memory/models.py", "note": "ConceptDerivation" },
    { "kind": "add", "path": "tests/validation/__init__.py", "note": "test package" },
    { "kind": "add", "path": "tests/validation/test_logic_engine.py", "note": "valid, contradiction, unsupported, malformed, injection" },
    { "kind": "add", "path": "tests/validation/test_service.py", "note": "delegation contract" },
    { "kind": "mod", "path": "pyproject.toml", "note": "pydatalog dependency + mypy override" },
    { "kind": "mod", "path": "uv.lock", "note": "resolved lock" },
    { "kind": "mod", "path": ".github/workflows/ci.yml", "note": "run tests/validation in the test and coverage jobs" }
  ],
  "signatures": [
    {
      "module": "src/cortex/validation/logic_engine.py",
      "language": "python",
      "code": "class LogicEngine:\n    def __init__(self) -> None: ...\n\n    def load_facts(self, facts: list[Fact]) -> None: ...\n\n    def validate_derivation(self, derivation: ConceptDerivation) -> ValidationResult: ..."
    },
    {
      "module": "src/cortex/validation/service.py",
      "language": "python",
      "code": "class ConceptValidationService:\n    def __init__(self, logic_engine: LogicEngine) -> None: ...\n\n    def validate(self, concept_derivation: ConceptDerivation) -> ValidationResult: ..."
    }
  ],
  "data_flow": {
    "nodes": [
      { "id": "logic_engine.load_facts", "module": "src/cortex/validation/logic_engine.py", "kind": "add" },
      { "id": "logic_engine.validate_derivation", "module": "src/cortex/validation/logic_engine.py", "kind": "add" },
      { "id": "service.validate", "module": "src/cortex/validation/service.py", "kind": "add" }
    ],
    "edges": [
      { "from": "service.validate", "to": "logic_engine.validate_derivation" }
    ]
  },
  "contracts": [
    {
      "site": "service.validate",
      "callee": "logic_engine.validate_derivation",
      "residual": "the module does not exist yet, so the checker treats the callee as external: any exception escaping validate_derivation is a bug in the engine's own catch-all, not a library exit",
      "exits": [
        { "kind": "returns", "type": "ValidationResult", "handled_at": "src/cortex/validation/service.py:validate returns the engine's result" },
        { "kind": "absent", "type": "never", "handled_at": "the delegate always returns a ValidationResult" },
        { "kind": "raises", "type": "none", "handled_at": "the engine catches parser and PyDatalog errors and returns invalid" },
        { "kind": "effect", "type": "none", "handled_at": null }
      ]
    },
    {
      "site": "logic_engine.load_facts",
      "call": "pyParser.load(atom, newglobals={}) — external PyDatalog parser",
      "residual": "the atom is built only from sanitised predicate/value strings (never raw LLM text); a malformed atom is skipped, and any DatalogError propagates to the caller of load_facts",
      "exits": [
        { "kind": "returns", "type": "None", "handled_at": "src/cortex/validation/logic_engine.py:load_facts" },
        { "kind": "absent", "type": "never", "handled_at": "every active fact either yields an atom or is skipped" },
        { "kind": "raises", "type": "DatalogError", "handled_at": "generated atoms are sanitised; the residual covers the external parser's other errors" },
        { "kind": "effect", "type": "resets the process-global PyDatalog engine", "handled_at": null }
      ]
    },
    {
      "site": "logic_engine.validate_derivation",
      "call": "pyDatalog.ask(query) — external PyDatalog query engine",
      "residual": "undefined predicates raise AttributeError and are treated as no answer; every other exception is caught and returned as ValidationResult(valid=False)",
      "exits": [
        { "kind": "returns", "type": "ValidationResult", "handled_at": "src/cortex/validation/logic_engine.py:validate_derivation" },
        { "kind": "absent", "type": "never", "handled_at": "every path returns a ValidationResult" },
        { "kind": "raises", "type": "none", "handled_at": "parser and engine exceptions are caught; the residual names the external boundary" },
        { "kind": "effect", "type": "mutates the process-global PyDatalog engine", "handled_at": null }
      ]
    }
  ]
}
```

## Premises

- **Premise**: PyDatalog remains importable and callable on the project's
  Python 3.12.
  - verdict: confirmed
  - evidence: `uv run --no-project --with pydatalog python -c "import pyDatalog"`
    → `pydatalog==0.22.4`; prototype `ask` returned `{()}` / `None` as expected.
- **Premise**: plain Datalog alone catches "user at home" vs "user at office".
  - verdict: falsified
  - evidence: prototype: two facts coexist; only a functional-predicate rule
    with `(P1 != P2)` produced `{('home','office'), ...}`.
- **Premise**: the ticket's `location(fact_id, place)` encoding catches the
  example.
  - verdict: falsified
  - evidence: prototype conflict rule over `location(X,P1) & location(X,P2)`
    returned `None` when the subjects were distinct fact ids.
- **Premise**: `pyDatalog.clear()` leaves undefined predicates queryable.
  - verdict: falsified
  - evidence: prototype raised
    `AttributeError: Predicate without definition (or error in resolver)`.
- **Premise**: LLM proof strings are safe to pass to `pyParser.load`.
  - verdict: falsified
  - evidence: `pyParser.load` does `ast.parse` → `compile` → `exec`
    (`~/.cache/.../pyDatalog/pyParser.py:load`); a strict grammar runs first.
- **Premise**: `MemoryService` is the concept insertion point.
  - verdict: falsified
  - evidence: deleted in `9a0357e` (#21); `ContextProvider.build_concept` at
    `src/cortex/memory/context_provider.py:243` is the current path, and #29
    owns the wiring.
- **Premise**: existing facts use Datalog `predicate(args)` symbolic reprs.
  - verdict: falsified
  - evidence: `FactExtractor._facts_from_location` emits `location.<place>`
    (`src/cortex/memory/fact_extractor.py`); the parser supports the dot form
    and the future atom form.
- **Premise**: no production caller exists yet, so #27 is additive.
  - verdict: confirmed
  - evidence: `grep -rn "LogicEngine\|ConceptValidationService" src` → no hits.
