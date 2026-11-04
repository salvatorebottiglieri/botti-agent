# Program Design — LLM revision loop for concept validation (#28)

## Context

#27 shipped the pure symbolic half: `LogicEngine` + `ConceptValidationService`
return a `ValidationResult` for a `ConceptDerivation`. #28 adds the feedback
loop: when the validator rejects a derivation, the LLM is asked to revise it
with the rejection context, and the revision is validated again. No event
emission or storage — those are #29.

Current code:
- `LLMClient.chat(messages, *, tools=None, generation_config=None) -> ChatResult`
  is **async** (`src/cortex/llm/base.py`).
- `ChatResult.message.content: str | None` (`src/cortex/llm/models.py`).
- `ConceptValidationService.validate(...)` is **sync and pure** (#27).
- `Concept` has `proof_chain: str` (not a list); `ConceptDerivation` has
  `proof_chain: list[str]`.
- There is no `MemoryService` (deleted in #21); storage/wiring is #29.

## Decisions

- **The retry loop lives in a new `ConceptDerivationService`; the pure
  `ConceptValidationService` does not gain an `LLMClient`.** The ticket's
  wording "ConceptValidationService wired to accept LLMClient" is superseded:
  keeping the validator pure is #27's contract, and the new service is the
  seam that owns the LLM dependency.
- **`derive_and_validate` is async.** The ticket's signature is sync, but
  `LLMClient.chat` is async and the repository's LLM seam is async throughout.
  The method awaits only the revision calls.
- **`max_retries` counts LLM revision calls, not the initial validation.**
  Default 3. `attempts` in the outcome is the number of LLM calls actually
  made (0 when the first validation passes). `max_retries=0` means "validate
  once, never revise"; a negative value raises `ValueError`.
- **A failed/malformed LLM revision is an attempt, not a crash.** The attempt
  counter increments, the reason is recorded, and the loop continues. After
  the budget, the outcome is `rejected` with the last reason.
- **Strict revision contract.** The LLM must answer JSON with a non-empty
  `proof_chain: list[str]`; `symbolic_repr`, `natural_lang_repr` and
  `confidence` are optional and fall back to the original concept. Markdown
  fences are stripped before `json.loads`. Anything else is a malformed
  attempt.
- **The returned concept is validated, not stored.** A valid outcome sets
  `validated=True` on the returned `Concept`; persistence and events are #29.
  A rejected outcome returns `concept=None`.
- **The validator's facts are the caller's responsibility.** The service does
  not fetch facts; the caller loads them into the shared `LogicEngine` before
  calling (the same contract as #27, and #29 will own the repository lookup).

## Call-stack tree

```diff
 ConceptDerivationService.derive_and_validate          # NEW — async
+  ConceptValidationService.validate                   # first attempt, 0 LLM calls
+  loop attempt in 1..max_retries:
+    _revision_messages(derivation, last_result)        # rejection context
+    LLMClient.chat(messages)                           # await, external
+    _parse_revision(content, original)                 # strict JSON
+    ConceptValidationService.validate(revised)         # re-validate
+  DerivationOutcome(status, concept, rejection_reason, attempts)
```

## File-tree diff

```diff
 src/cortex/validation
+  derivation_service.py   # NEW — ConceptDerivationService + prompt/parse
 ~  models.py             # MODIFIED — DerivationOutcome
 ~  __init__.py           # MODIFIED — export ConceptDerivationService, DerivationOutcome
 tests/validation
+  test_derivation_service.py   # NEW — valid first try, revision, exhaustion, malformed, LLM failure
 docs/design
+  validation-retry-loop.md     # this document
```

## Type signatures

```python
# src/cortex/validation/models.py
DerivationStatus = Literal["valid", "rejected"]

@dataclass
class DerivationOutcome:
    status: DerivationStatus
    concept: Concept | None = None
    rejection_reason: str | None = None
    attempts: int = 0


# src/cortex/validation/derivation_service.py
class ConceptDerivationService:
    def __init__(
        self,
        validation_service: ConceptValidationService,
        llm_client: LLMClient,
    ) -> None: ...

    async def derive_and_validate(
        self,
        proposed_derivation: ConceptDerivation,
        max_retries: int = 3,
    ) -> DerivationOutcome: ...


# src/cortex/validation/service.py — unchanged (pure, sync)
class ConceptValidationService:
    def validate(self, concept_derivation: ConceptDerivation) -> ValidationResult: ...
```

## Data flow

```
derive_and_validate ──► ConceptValidationService.validate   (internal)
        │
        ├──► LLMClient.chat(messages)                        (external, await)
        │       └── ChatResult.message.content
        ├──► _parse_revision (JSON → ConceptDerivation)
        └──► ValidationResult / DerivationOutcome
```

## Contracts

| site | callee | returns | absent | raises | effect |
|---|---|---|---|---|---|
| `derive_and_validate` | `validation_service.validate` | `ValidationResult` | never — always returns | none — pure sync | none |
| `derive_and_validate` | `LLMClient.chat` (external) | `ChatResult` | never — a response or an exception | provider/transport exceptions are caught, counted as an attempt and returned as `rejected` after the budget | none (no events/storage) |

```json design
{
  "feature": "validation-retry-loop",
  "call_stack": {
    "name": "ConceptDerivationService.derive_and_validate",
    "children": [
      { "name": "ConceptValidationService.validate", "note": "first attempt; pure sync" },
      { "name": "LLMClient.chat", "note": "revision call, awaited; external" },
      { "name": "ConceptValidationService.validate", "note": "re-validate each revision" }
    ]
  },
  "file_tree": [
    { "kind": "add", "path": "src/cortex/validation/derivation_service.py", "note": "ConceptDerivationService, prompt, strict JSON parsing" },
    { "kind": "mod", "path": "src/cortex/validation/models.py", "note": "DerivationOutcome" },
    { "kind": "mod", "path": "src/cortex/validation/__init__.py", "note": "public exports" },
    { "kind": "add", "path": "tests/validation/test_derivation_service.py", "note": "loop, exhaustion, malformed output, LLM failure" },
    { "kind": "add", "path": "docs/design/validation-retry-loop.md", "note": "this design" }
  ],
  "signatures": [
    {
      "module": "src/cortex/validation/derivation_service.py",
      "language": "python",
      "code": "class ConceptDerivationService:\n    def __init__(\n        self,\n        validation_service: ConceptValidationService,\n        llm_client: LLMClient,\n    ) -> None: ...\n\n    async def derive_and_validate(\n        self,\n        proposed_derivation: ConceptDerivation,\n        max_retries: int = 3,\n    ) -> DerivationOutcome: ..."
    },
    {
      "module": "src/cortex/validation/service.py",
      "language": "python",
      "code": "class ConceptValidationService:\n    def validate(self, concept_derivation: ConceptDerivation) -> ValidationResult: ..."
    }
  ],
  "data_flow": {
    "nodes": [
      { "id": "derivation_service.derive_and_validate", "module": "src/cortex/validation/derivation_service.py", "kind": "add" },
      { "id": "validation_service.validate", "module": "src/cortex/validation/service.py", "kind": "keep" }
    ],
    "edges": [
      { "from": "derivation_service.derive_and_validate", "to": "validation_service.validate" }
    ]
  },
  "contracts": [
    {
      "site": "derivation_service.derive_and_validate",
      "callee": "validation_service.validate",
      "exits": [
        { "kind": "returns", "type": "ValidationResult", "handled_at": "src/cortex/validation/derivation_service.py:derive_and_validate" },
        { "kind": "absent", "type": "never", "handled_at": "the validator always returns a result" },
        { "kind": "raises", "type": "none", "handled_at": "pure sync validator" },
        { "kind": "effect", "type": "none", "handled_at": null }
      ]
    },
    {
      "site": "derivation_service.derive_and_validate",
      "call": "await llm_client.chat(messages) — external LLM provider",
      "residual": "provider, transport and circuit-breaker exceptions are caught; the attempt is counted and recorded, the loop continues, and after the budget the outcome is rejected — no exception escapes derive_and_validate",
      "exits": [
        { "kind": "returns", "type": "ChatResult", "handled_at": "src/cortex/validation/derivation_service.py:_revision" },
        { "kind": "absent", "type": "never", "handled_at": "a call returns a ChatResult or raises" },
        { "kind": "raises", "type": "Exception", "handled_at": "caught in derive_and_validate; counted as a failed attempt and returned as rejected after the budget" },
        { "kind": "effect", "type": "none (no events, no storage)", "handled_at": null }
      ]
    }
  ]
}
```

## Premises

- **Premise**: `LLMClient.chat` is async and returns `ChatResult` with
  `message.content: str | None`.
  - verdict: confirmed
  - evidence: `src/cortex/llm/base.py` (`async def chat`); `src/cortex/llm/models.py`
    (`class ChatResult`).
- **Premise**: `ConceptValidationService.validate` is sync and pure, so it can
  stay unchanged.
  - verdict: confirmed
  - evidence: `src/cortex/validation/service.py`; #27 design.
- **Premise**: the retry loop needs a fact repository or storage.
  - verdict: falsified
  - evidence: the loop only needs the validator's already-loaded facts; storage
    and events are #29 (ticket boundaries).
- **Premise**: `MemoryService` is the storage insertion point.
  - verdict: falsified
  - evidence: deleted in #21 (`9a0357e`); `ContextProvider.build_concept` is the
    current path, and #29 owns it.
- **Premise**: the ticket's "ConceptValidationService accepts LLMClient" is the
  right seam.
  - verdict: falsified / reframed
  - evidence: #27's approved contract keeps the validator pure; the new
    `ConceptDerivationService` owns the LLM dependency.
- **Premise**: the ticket defines the LLM's revision output format.
  - verdict: falsified
  - evidence: the ticket shows only the rejection prompt; this design defines
    the strict JSON revision schema.
- **Premise**: `Concept.proof_chain` is a list like `ConceptDerivation.proof_chain`.
  - verdict: falsified
  - evidence: `src/cortex/memory/models.py` — `Concept.proof_chain: str`; the
    service joins the list with newlines for the stored field.
- **Premise**: `max_retries` has an unambiguous meaning.
  - verdict: falsified
  - evidence: the ticket says both "max 3 retries" and "max 3 total attempts";
    this design defines it as LLM revision calls, with `attempts` counting them.
