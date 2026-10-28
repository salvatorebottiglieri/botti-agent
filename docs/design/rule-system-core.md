# Program design — rule system v1, core (issue #130, slice A)

Slice A delivers the rule system **without the LLM classifier**: schema, pure
score, repository, lifecycle service (create / record use / promote / demote /
archive / split / merge), prompt rendering, context-bundle dimension, CLI and
tests. Slice B (feedback classifier, use attribution, proposals, conflict
check, eval fixtures) is designed separately and reuses these signatures.

Sources of truth: `docs/specs/rule-system.md` (contract), `docs/adr/0020-learned-rules.md`
(why). Vocabulary: `CONTEXT.md` (`## Rules`).

## Deviations from the spec, decided here

1. **Module home** — the spec names `cortex/learning/rules/scoring.py` as the
   home of the shared update function. The rule system is its own subsystem
   (own tables, own CLI, own LLM client in slice B), and `cortex/learning/`
   is the Echo State Network module (ADR-0001) which has no LLM client and is
   not mounted in the composition root. The package is `src/cortex/rules/`,
   mirroring `src/cortex/trace/`. The spec is updated in the same PR.
2. **One prior** — the spec's "prior (dictated) 0.95" is implemented as
   `PRIOR = 0.5` plus one `approval` use written with the rule
   (`logit(0.5) + ln(20) ≈ 0.95`). There is no second prior constant, and the
   dictation expires with the 6-month window as the spec requires.
3. **Split/merge do not call an LLM in slice A.** The classifier (slice B)
   decides *which* child each use belongs to; `RuleService.split` takes that
   decision as a mapping and only performs the mechanical redistribution. This
   keeps the lifecycle testable without a provider.
4. **Universal-condition rules** are not modelled in v1 (the spec's "always
   enter" guarantee needs a field the schema does not have). The budget is a
   plain confidence-ordered cap of `CONTEXT_BUDGET = 40`; noted in the spec.

## Call-stack tree

```diff
 ExecutionModule / CLI / classifier (slice B)
+  RuleService.create_dictated(session_id, spec)
+    PostgresRuleRepository.create(rule)
+    PostgresRuleRepository.add_use(dictation use)
+  RuleService.create_from_correction(session_id, spec)
+    PostgresRuleRepository.create(rule)
+  RuleService.record_use(rule_id, session_id, situation, outcome)
+    PostgresRuleRepository.get(rule_id)
+    PostgresRuleRepository.add_use(use)
+    scoring.posterior(PRIOR, uses, now)
+    scoring.next_state(state, confidence, uses, now)
+    PostgresRuleRepository.refresh(rule, confidence, state)
+  RuleService.list_active()
+    PostgresRuleRepository.list_by_states([STAGING, DEFINITIVE])
+      (per rule: list_uses → posterior → next_state → refresh)
+  RuleService.split(rule_id, children, assignments)
+  RuleService.merge(parent_ids, spec)
 ContextProvider.get_memory_context(session_id, query, ...)
+    get_rules_context()
+      RuleService.list_active()
 Reasoner._build_prompt(context)
+    render_rules(context.memory.rules)
 CLI
+  rules:list / rules:show / rules:archive
```

## File-tree diff

```diff
 migrations
+  └── 008_rules.sql              # NEW — rules + rule_uses tables and indexes
 src/cortex/rules
+  ├── __init__.py                # NEW — package marker
+  ├── models.py                  # NEW — Rule, RuleSpec, RuleUse, enums, RuleNotFoundError
+  ├── constants.py               # NEW — the isolated calibration constants (one table)
+  ├── scoring.py                 # NEW — pure logit update, active window, lifecycle decision
+  ├── interfaces.py              # NEW — RuleRepository ABC
+  ├── repository.py              # NEW — PostgresRuleRepository over an injectable session factory
+  ├── service.py                 # NEW — RuleService (lifecycle mechanics)
+  └── render.py                  # NEW — render_rules: the prompt line, staged marked
 src/cortex/agentic
~  ├── models.py                  # MODIFIED — MemoryContext.rules dimension
~  └── reasoner.py                # MODIFIED — rules section in the system prompt
 src/cortex/memory
~  └── context_provider.py        # MODIFIED — get_rules_context + bundle wiring (4th dimension)
 src/cortex/cli.py               # MODIFIED — rules:list / rules:show / rules:archive
 src/main.py                     # MODIFIED — build PostgresRuleRepository + RuleService, inject
 docs/specs/rule-system.md       # MODIFIED — status, module path, v1 gap notes
 docs/specs/architecture.md      # MODIFIED — rules module in the map
 tests/unit/rules
+  ├── __init__.py                # NEW
+  ├── fakes.py                   # NEW — InMemoryRuleRepository (test double for the ABC)
+  ├── test_scoring.py            # NEW — score invariants (spec's Invariants table)
+  ├── test_service.py            # NEW — lifecycle, split, merge, render
+  ├── test_repository.py         # NEW — SQL shape over a mocked session
+  └── test_cli_rules.py          # NEW — list/show/archive
 tests/unit/agentic/test_reasoner*.py   # MODIFIED/ADDED — rules section in the prompt
 tests/memory/test_context_provider.py  # MODIFIED — rules dimension + degradation
```

## Type signatures

```json design
{
  "signatures": [
    {
      "module": "src/cortex/rules/models.py",
      "language": "python",
      "code": "class RuleState(StrEnum):\n    STAGING = \"staging\"\n    DEFINITIVE = \"definitive\"\n    ARCHIVED = \"archived\"\n\nclass RuleOrigin(StrEnum):\n    DICTATED = \"dictated\"\n    CORRECTION = \"correction\"\n    MERGE = \"merge\"\n\nclass UseOutcome(StrEnum):\n    APPROVAL = \"approval\"\n    IMPLICIT = \"implicit\"\n    CORRECTION = \"correction\"\n    SCOPE_CORRECTION = \"scope_correction\"\n    NEUTRAL = \"neutral\"\n\nclass RuleNotFoundError(LookupError):\n    pass\n\n@dataclass(frozen=True)\nclass RuleSpec:\n    condition: str\n    action: str\n    rationale: str = \"\"\n\n@dataclass\nclass Rule:\n    id: UUID = field(default_factory=uuid.uuid4)\n    condition: str = \"\"\n    action: str = \"\"\n    rationale: str = \"\"\n    state: RuleState = RuleState.STAGING\n    confidence: float = 0.5\n    origin: RuleOrigin = RuleOrigin.CORRECTION\n    superseded_by: UUID | None = None\n    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))\n    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))\n    archived_at: datetime | None = None\n\n    def declares_itself(self) -> bool:\n        ...\n\n@dataclass\nclass RuleUse:\n    rule_id: UUID\n    session_id: UUID\n    situation: str\n    outcome: UseOutcome\n    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))\n    id: int | None = None"
    },
    {
      "module": "src/cortex/rules/scoring.py",
      "language": "python",
      "code": "def active_uses(uses: Sequence[RuleUse], now: datetime) -> list[RuleUse]:\n    ...\n\ndef use_counts(uses: Sequence[RuleUse], now: datetime) -> tuple[int, int]:\n    ...\n\ndef posterior(prior: float, uses: Sequence[RuleUse], now: datetime) -> float:\n    ...\n\ndef next_state(current: RuleState, confidence: float, uses: Sequence[RuleUse], now: datetime) -> RuleState:\n    ..."
    },
    {
      "module": "src/cortex/rules/interfaces.py",
      "language": "python",
      "code": "class RuleRepository(ABC):\n    async def create(self, rule: Rule) -> Rule:\n        ...\n\n    async def get(self, rule_id: UUID) -> Rule | None:\n        ...\n\n    async def add_use(self, use: RuleUse) -> RuleUse:\n        ...\n\n    async def list_uses(self, rule_id: UUID) -> list[RuleUse]:\n        ...\n\n    async def list_by_states(self, states: Sequence[RuleState], *, limit: int = 500) -> list[Rule]:\n        ...\n\n    async def refresh(self, rule: Rule) -> Rule:\n        ...\n\n    async def reassign_uses(self, use_ids: Sequence[int], rule_id: UUID) -> int:\n        ..."
    },
    {
      "module": "src/cortex/rules/repository.py",
      "language": "python",
      "code": "class PostgresRuleRepository(RuleRepository):\n    def __init__(self, session_factory: Callable[[], AsyncContextManager[Any]] = DbSession) -> None:\n        ...\n\n    async def create(self, rule: Rule) -> Rule:\n        ...\n\n    async def get(self, rule_id: UUID) -> Rule | None:\n        ...\n\n    async def add_use(self, use: RuleUse) -> RuleUse:\n        ...\n\n    async def list_uses(self, rule_id: UUID) -> list[RuleUse]:\n        ...\n\n    async def list_by_states(self, states: Sequence[RuleState], *, limit: int = 500) -> list[Rule]:\n        ...\n\n    async def refresh(self, rule: Rule) -> Rule:\n        ...\n\n    async def reassign_uses(self, use_ids: Sequence[int], rule_id: UUID) -> int:\n        ..."
    },
    {
      "module": "src/cortex/rules/service.py",
      "language": "python",
      "code": "class RuleService:\n    def __init__(self, repository: RuleRepository, *, clock: Callable[[], datetime] = _utcnow) -> None:\n        ...\n\n    async def create_dictated(self, session_id: UUID, spec: RuleSpec) -> Rule:\n        ...\n\n    async def create_from_correction(self, session_id: UUID, spec: RuleSpec) -> Rule:\n        ...\n\n    async def record_use(self, rule_id: UUID, session_id: UUID, situation: str, outcome: UseOutcome) -> Rule:\n        ...\n\n    async def list_active(self) -> list[Rule]:\n        ...\n\n    async def get(self, rule_id: UUID) -> Rule | None:\n        ...\n\n    async def archive(self, rule_id: UUID) -> Rule | None:\n        ...\n\n    async def split(self, rule_id: UUID, children: Sequence[RuleSpec], assignments: Mapping[int, int]) -> list[Rule]:\n        ...\n\n    async def merge(self, parent_ids: Sequence[UUID], spec: RuleSpec) -> Rule:\n        ..."
    },
    {
      "module": "src/cortex/rules/render.py",
      "language": "python",
      "code": "def render_rules(rules: Sequence[Rule]) -> str | None:\n    ..."
    },
    {
      "module": "src/cortex/memory/context_provider.py",
      "language": "python",
      "code": "class ContextProvider:\n    def __init__(\n        self,\n        fact_repository: FactRepository,\n        fact_store: FactStore,\n        concept_repository: ConceptRepository | None = None,\n        rule_service: RuleService | None = None,\n    ):\n        ...\n\n    async def get_rules_context(self) -> list[Rule]:\n        ...\n\n    async def get_memory_context(\n        self,\n        session_id: UUID | None,\n        query: str,\n        *,\n        max_facts: int = 10,\n        fact_types: list[FactType] | None = None,\n    ) -> MemoryContext:\n        ..."
    },
    {
      "module": "src/cortex/agentic/reasoner.py",
      "language": "python",
      "code": "class Reasoner:\n    def _build_prompt(self, context: Context) -> list[ChatMessage]:\n        ..."
    },
    {
      "module": "src/cortex/cli.py",
      "language": "python",
      "code": "def rules_list(db_url: str | None = typer.Option(None, \"--db-url\", help=\"Database URL\")) -> None:\n    ...\n\ndef rules_show(rule_id: str = typer.Argument(..., help=\"Rule ID\"), db_url: str | None = typer.Option(None, \"--db-url\", help=\"Database URL\")) -> None:\n    ...\n\ndef rules_archive(rule_id: str = typer.Argument(..., help=\"Rule ID\"), db_url: str | None = typer.Option(None, \"--db-url\", help=\"Database URL\")) -> None:\n    ..."
    }
  ],
  "data_flow": {
    "nodes": [
      {"id": "RuleService.create_dictated", "kind": "add", "module": "src/cortex/rules/service.py"},
      {"id": "RuleService.create_from_correction", "kind": "add", "module": "src/cortex/rules/service.py"},
      {"id": "RuleService.record_use", "kind": "add", "module": "src/cortex/rules/service.py"},
      {"id": "RuleService.list_active", "kind": "add", "module": "src/cortex/rules/service.py"},
      {"id": "RuleService.get", "kind": "add", "module": "src/cortex/rules/service.py"},
      {"id": "RuleService.archive", "kind": "add", "module": "src/cortex/rules/service.py"},
      {"id": "RuleService.split", "kind": "add", "module": "src/cortex/rules/service.py"},
      {"id": "RuleService.merge", "kind": "add", "module": "src/cortex/rules/service.py"},
      {"id": "PostgresRuleRepository.create", "kind": "add", "module": "src/cortex/rules/repository.py"},
      {"id": "PostgresRuleRepository.get", "kind": "add", "module": "src/cortex/rules/repository.py"},
      {"id": "PostgresRuleRepository.add_use", "kind": "add", "module": "src/cortex/rules/repository.py"},
      {"id": "PostgresRuleRepository.list_uses", "kind": "add", "module": "src/cortex/rules/repository.py"},
      {"id": "PostgresRuleRepository.list_by_states", "kind": "add", "module": "src/cortex/rules/repository.py"},
      {"id": "PostgresRuleRepository.refresh", "kind": "add", "module": "src/cortex/rules/repository.py"},
      {"id": "PostgresRuleRepository.reassign_uses", "kind": "add", "module": "src/cortex/rules/repository.py"},
      {"id": "posterior", "kind": "add", "module": "src/cortex/rules/scoring.py"},
      {"id": "next_state", "kind": "add", "module": "src/cortex/rules/scoring.py"},
      {"id": "render_rules", "kind": "add", "module": "src/cortex/rules/render.py"},
      {"id": "ContextProvider.get_rules_context", "kind": "add", "module": "src/cortex/memory/context_provider.py"},
      {"id": "ContextProvider.get_memory_context", "kind": "mod", "module": "src/cortex/memory/context_provider.py"},
      {"id": "Reasoner._build_prompt", "kind": "mod", "module": "src/cortex/agentic/reasoner.py"},
      {"id": "rules_list", "kind": "add", "module": "src/cortex/cli.py"},
      {"id": "rules_show", "kind": "add", "module": "src/cortex/cli.py"},
      {"id": "rules_archive", "kind": "add", "module": "src/cortex/cli.py"}
    ],
    "edges": [
      {"from": "RuleService.create_dictated", "to": "PostgresRuleRepository.create"},
      {"from": "RuleService.create_dictated", "to": "PostgresRuleRepository.add_use"},
      {"from": "RuleService.create_from_correction", "to": "PostgresRuleRepository.create"},
      {"from": "RuleService.record_use", "to": "PostgresRuleRepository.get"},
      {"from": "RuleService.record_use", "to": "PostgresRuleRepository.add_use"},
      {"from": "RuleService.record_use", "to": "posterior"},
      {"from": "RuleService.record_use", "to": "next_state"},
      {"from": "RuleService.record_use", "to": "PostgresRuleRepository.refresh"},
      {"from": "RuleService.list_active", "to": "PostgresRuleRepository.list_by_states"},
      {"from": "RuleService.list_active", "to": "PostgresRuleRepository.list_uses"},
      {"from": "RuleService.list_active", "to": "posterior"},
      {"from": "RuleService.list_active", "to": "next_state"},
      {"from": "RuleService.list_active", "to": "PostgresRuleRepository.refresh"},
      {"from": "RuleService.get", "to": "PostgresRuleRepository.get"},
      {"from": "RuleService.archive", "to": "PostgresRuleRepository.get"},
      {"from": "RuleService.archive", "to": "PostgresRuleRepository.refresh"},
      {"from": "RuleService.split", "to": "PostgresRuleRepository.get"},
      {"from": "RuleService.split", "to": "PostgresRuleRepository.list_uses"},
      {"from": "RuleService.split", "to": "PostgresRuleRepository.create"},
      {"from": "RuleService.split", "to": "PostgresRuleRepository.reassign_uses"},
      {"from": "RuleService.split", "to": "PostgresRuleRepository.refresh"},
      {"from": "RuleService.merge", "to": "PostgresRuleRepository.get"},
      {"from": "RuleService.merge", "to": "PostgresRuleRepository.list_uses"},
      {"from": "RuleService.merge", "to": "PostgresRuleRepository.create"},
      {"from": "RuleService.merge", "to": "PostgresRuleRepository.reassign_uses"},
      {"from": "RuleService.merge", "to": "PostgresRuleRepository.refresh"},
      {"from": "ContextProvider.get_rules_context", "to": "RuleService.list_active"},
      {"from": "ContextProvider.get_memory_context", "to": "ContextProvider.get_rules_context"},
      {"from": "Reasoner._build_prompt", "to": "render_rules"},
      {"from": "rules_list", "to": "RuleService.list_active"},
      {"from": "rules_show", "to": "RuleService.get"},
      {"from": "rules_archive", "to": "RuleService.archive"}
    ]
  },
  "contracts": [
    {
      "site": "RuleService.create_dictated",
      "callees": ["PostgresRuleRepository.create", "PostgresRuleRepository.add_use"],
      "exits": [
        {"kind": "returns", "type": "Rule (definitive, confidence 0.95)", "handled_at": "RuleService caller — slice B dictation path and tests/unit/rules/test_service.py"},
        {"kind": "absent", "type": "—", "handled_at": "impossible: a dictated rule always yields a Rule; a blank condition or action raises before any write"},
        {"kind": "raises", "type": "ValueError on blank condition/action", "handled_at": "cortex/cli.py rules_list error path (typer exits 1); slice B proposal path logs and drops"},
        {"kind": "effect", "type": "inserts one rules row plus one approval rule_uses row (the dictation)"}
      ]
    },
    {
      "site": "RuleService.create_from_correction",
      "callees": ["PostgresRuleRepository.create"],
      "exits": [
        {"kind": "returns", "type": "Rule (staging, confidence 0.5, no use)", "handled_at": "RuleService caller — slice B proposal path and tests/unit/rules/test_service.py"},
        {"kind": "absent", "type": "—", "handled_at": "impossible: a correction-born rule always yields a Rule"},
        {"kind": "raises", "type": "ValueError on blank condition/action", "handled_at": "cortex/cli.py rules_list error path; slice B proposal path logs and drops"},
        {"kind": "effect", "type": "inserts one rules row in staging"}
      ]
    },
    {
      "site": "RuleService.record_use",
      "callees": ["PostgresRuleRepository.get", "PostgresRuleRepository.add_use", "posterior", "next_state", "PostgresRuleRepository.refresh"],
      "exits": [
        {"kind": "returns", "type": "Rule with recomputed confidence and state", "handled_at": "RuleService caller — slice B attribution loop and tests/unit/rules/test_service.py"},
        {"kind": "absent", "type": "RuleNotFoundError", "handled_at": "raised by RuleService.record_use; slice B attribution catches it and skips the use"},
        {"kind": "raises", "type": "RuleNotFoundError, asyncpg.PostgresError", "handled_at": "slice B attribution wraps the call in try/except and leaves the use queued; CLI archives do not call it"},
        {"kind": "effect", "type": "appends one rule_uses row and may rewrite the rule's state/confidence"}
      ]
    },
    {
      "site": "RuleService.list_active",
      "callees": ["PostgresRuleRepository.list_by_states", "PostgresRuleRepository.list_uses", "posterior", "next_state", "PostgresRuleRepository.refresh"],
      "exits": [
        {"kind": "returns", "type": "list[Rule] (staging + definitive, confidence desc, at most 40)", "handled_at": "ContextProvider.get_rules_context and CLI rules:list"},
        {"kind": "absent", "type": "[]", "handled_at": "returned as an empty list; render_rules then yields None and the prompt has no rules section"},
        {"kind": "raises", "type": "asyncpg.PostgresError", "handled_at": "ContextProvider.get_memory_context catches it per dimension and records 'rules' in degraded_dimensions"},
        {"kind": "effect", "type": "refreshes rules whose recomputed state or confidence changed (expiry, promotion, demotion, archive)"}
      ]
    },
    {
      "site": "RuleService.get",
      "callee": "PostgresRuleRepository.get",
      "exits": [
        {"kind": "returns", "type": "Rule", "handled_at": "cortex/cli.py rules_show prints the fields, uses and superseded_by"},
        {"kind": "absent", "type": "None for an unknown id", "handled_at": "cortex/cli.py rules_show prints 'rule not found' and exits 1"},
        {"kind": "raises", "type": "asyncpg.PostgresError", "handled_at": "cortex/cli.py rules_show runs under asyncio.run; the exception reaches typer and exits non-zero with the message"},
        {"kind": "effect", "type": "read-only"}
      ]
    },
    {
      "site": "RuleService.archive",
      "callees": ["PostgresRuleRepository.get", "PostgresRuleRepository.refresh"],
      "exits": [
        {"kind": "returns", "type": "Rule with state archived", "handled_at": "cortex/cli.py rules_archive prints the archived id"},
        {"kind": "absent", "type": "None for an unknown id", "handled_at": "cortex/cli.py rules_archive prints 'rule not found' and exits 1"},
        {"kind": "raises", "type": "asyncpg.PostgresError", "handled_at": "cortex/cli.py rules_archive exits non-zero with the message"},
        {"kind": "effect", "type": "sets state=archived, archived_at=now; the rule is never loaded into context again"}
      ]
    },
    {
      "site": "RuleService.split",
      "callees": ["PostgresRuleRepository.get", "PostgresRuleRepository.list_uses", "PostgresRuleRepository.create", "PostgresRuleRepository.reassign_uses", "PostgresRuleRepository.refresh"],
      "exits": [
        {"kind": "returns", "type": "list[Rule] — the children, index 0 preserving the parent's state", "handled_at": "RuleService caller — slice B scope-correction path and tests/unit/rules/test_service.py"},
        {"kind": "absent", "type": "RuleNotFoundError for an unknown parent", "handled_at": "raised here; slice B logs and leaves the correction unanswered, CLI never splits"},
        {"kind": "raises", "type": "RuleNotFoundError, ValueError on fewer than two children or an assignment out of range", "handled_at": "raised before any write; slice B logs and drops the proposal"},
        {"kind": "effect", "type": "archives the parent (superseded_by = first child), creates the children, reassigns each use to its assigned child"}
      ]
    },
    {
      "site": "RuleService.merge",
      "callees": ["PostgresRuleRepository.get", "PostgresRuleRepository.list_uses", "PostgresRuleRepository.create", "PostgresRuleRepository.reassign_uses", "PostgresRuleRepository.refresh"],
      "exits": [
        {"kind": "returns", "type": "Rule — the fused rule, definitive only if every parent was", "handled_at": "RuleService caller — slice B user-approved merge and tests/unit/rules/test_service.py"},
        {"kind": "absent", "type": "RuleNotFoundError", "handled_at": "raised here; slice B logs and drops the merge proposal"},
        {"kind": "raises", "type": "RuleNotFoundError, ValueError on fewer than two parents", "handled_at": "raised before any write; slice B logs and drops"},
        {"kind": "effect", "type": "creates the fused rule, reassigns every parent's uses to it, archives the parents with superseded_by = the fused rule, refreshes the fused confidence from the union"}
      ]
    },
    {
      "site": "posterior",
      "call": "pure: logit of the prior plus the sum of direction times ln(LR) over active uses, then sigmoid",
      "residual": "internal pure function: no external call, so no non-enumerated exit; the only raise is the prior-domain ValueError declared below",
      "exits": [
        {"kind": "returns", "type": "float in [0, 1], the recomputed posterior over active uses", "handled_at": "RuleService.record_use and RuleService.list_active assign it to Rule.confidence"},
        {"kind": "absent", "type": "—", "handled_at": "impossible: an empty use list returns the prior (0.5), never None"},
        {"kind": "raises", "type": "ValueError when prior is outside (0, 1)", "handled_at": "programming error; the only callers pass constants.PRIOR, and tests/unit/rules/test_scoring.py pins it"},
        {"kind": "effect", "type": "pure"}
      ]
    },
    {
      "site": "RuleService.list_active",
      "callee": "next_state",
      "exits": [
        {"kind": "returns", "type": "RuleState — staged, promoted, demoted or archived", "handled_at": "RuleService.list_active applies it through PostgresRuleRepository.refresh"},
        {"kind": "absent", "type": "—", "handled_at": "impossible: next_state is total, it returns the input state when no threshold is crossed"},
        {"kind": "raises", "type": "—", "handled_at": "cannot raise: pure function over an already-validated RuleState and float"},
        {"kind": "effect", "type": "pure"}
      ]
    },
    {
      "site": "PostgresRuleRepository.create",
      "call": "asyncpg conn.fetchrow of INSERT INTO rules ... RETURNING ...",
      "exits": [
        {"kind": "returns", "type": "Rule mapped from the RETURNING row", "handled_at": "RuleService.create_dictated / create_from_correction"},
        {"kind": "absent", "type": "—", "handled_at": "impossible: INSERT ... RETURNING always yields one row"},
        {"kind": "raises", "type": "asyncpg.PostgresError, ConnectionError", "handled_at": "propagates to RuleService and its caller; no retry in v1"},
        {"kind": "effect", "type": "one rules row"}
      ],
      "residual": "asyncpg exceptions not enumerated (UniqueViolationError on the primary key, pool exhaustion, serialization failures) propagate unchanged — the repository never swallows or rewraps."
    },
    {
      "site": "PostgresRuleRepository.get",
      "call": "asyncpg conn.fetchrow of SELECT ... FROM rules WHERE id = $1",
      "exits": [
        {"kind": "returns", "type": "Rule", "handled_at": "RuleService.get and RuleService.record_use"},
        {"kind": "absent", "type": "None", "handled_at": "RuleService.get returns None to CLI rules:show; RuleService.record_use raises RuleNotFoundError"},
        {"kind": "raises", "type": "asyncpg.PostgresError, ConnectionError", "handled_at": "propagates to the caller; list_active's dimension isolation catches it in the context path"},
        {"kind": "effect", "type": "read-only"}
      ],
      "residual": "asyncpg exceptions not enumerated (pool exhaustion, decoding errors) propagate unchanged."
    },
    {
      "site": "PostgresRuleRepository.add_use",
      "call": "asyncpg conn.fetchrow of INSERT INTO rule_uses ... RETURNING ...",
      "exits": [
        {"kind": "returns", "type": "RuleUse with its generated id", "handled_at": "RuleService.record_use ignores the id; RuleService.create_dictated needs the row only for its side effect"},
        {"kind": "absent", "type": "—", "handled_at": "impossible: INSERT ... RETURNING always yields one row"},
        {"kind": "raises", "type": "asyncpg.PostgresError, ConnectionError (ForeignViolationError on an unknown rule_id)", "handled_at": "propagates to RuleService and the caller; the rule is fetched first, so the FK violation is a programming error"},
        {"kind": "effect", "type": "one rule_uses row"}
      ],
      "residual": "asyncpg exceptions not enumerated propagate unchanged."
    },
    {
      "site": "PostgresRuleRepository.list_uses",
      "call": "asyncpg conn.fetch of SELECT ... FROM rule_uses WHERE rule_id = $1 ORDER BY occurred_at",
      "exits": [
        {"kind": "returns", "type": "list[RuleUse] oldest first", "handled_at": "RuleService.record_use, list_active, split and merge recompute from it"},
        {"kind": "absent", "type": "[]", "handled_at": "posterior returns the prior for an empty list; split/merge simply reassign nothing"},
        {"kind": "raises", "type": "asyncpg.PostgresError, ConnectionError", "handled_at": "propagates to RuleService and its caller"},
        {"kind": "effect", "type": "read-only"}
      ],
      "residual": "asyncpg exceptions not enumerated propagate unchanged."
    },
    {
      "site": "PostgresRuleRepository.list_by_states",
      "call": "asyncpg conn.fetch of SELECT ... FROM rules WHERE state = ANY $1 ORDER BY confidence DESC LIMIT $2",
      "exits": [
        {"kind": "returns", "type": "list[Rule] confidence desc", "handled_at": "RuleService.list_active trims to CONTEXT_BUDGET"},
        {"kind": "absent", "type": "[]", "handled_at": "RuleService.list_active returns []; the prompt gets no rules section"},
        {"kind": "raises", "type": "asyncpg.PostgresError, ConnectionError", "handled_at": "propagates to RuleService.list_active and then to the dimension isolation in ContextProvider"},
        {"kind": "effect", "type": "read-only"}
      ],
      "residual": "asyncpg exceptions not enumerated propagate unchanged."
    },
    {
      "site": "PostgresRuleRepository.refresh",
      "call": "asyncpg conn.fetchrow of UPDATE rules SET state = $2, confidence = $3, archived_at = $4, updated_at = NOW() WHERE id = $1 RETURNING ...",
      "exits": [
        {"kind": "returns", "type": "Rule from the RETURNING row", "handled_at": "RuleService.record_use / list_active / archive return it"},
        {"kind": "absent", "type": "None for a row deleted concurrently", "handled_at": "RuleService raises RuleNotFoundError on None; list_active skips the rule and logs"},
        {"kind": "raises", "type": "asyncpg.PostgresError, ConnectionError", "handled_at": "propagates to RuleService and its caller"},
        {"kind": "effect", "type": "rewrites state, confidence, archived_at, updated_at"}
      ],
      "residual": "asyncpg exceptions not enumerated propagate unchanged."
    },
    {
      "site": "PostgresRuleRepository.reassign_uses",
      "call": "asyncpg conn.execute of UPDATE rule_uses SET rule_id = $1 WHERE id = ANY $2",
      "exits": [
        {"kind": "returns", "type": "int — rows reassigned", "handled_at": "RuleService.split / merge use it only as a post-condition check in tests"},
        {"kind": "absent", "type": "0 when the id list is empty", "handled_at": "RuleService.split / merge tolerate it: a child with no uses simply scores at the prior"},
        {"kind": "raises", "type": "asyncpg.PostgresError, ConnectionError", "handled_at": "propagates to RuleService and its caller; the split/merge has already written rows, so the caller sees the failure and the next sweep converges"},
        {"kind": "effect", "type": "rewrites rule_uses.rule_id"}
      ],
      "residual": "asyncpg exceptions not enumerated propagate unchanged."
    },
    {
      "site": "ContextProvider.get_rules_context",
      "callee": "RuleService.list_active",
      "exits": [
        {"kind": "returns", "type": "list[Rule]", "handled_at": "ContextProvider.get_memory_context assigns it to MemoryContext.rules"},
        {"kind": "absent", "type": "[] when no RuleService is injected", "handled_at": "ContextProvider.get_rules_context returns []; MemoryContext.rules stays empty and the prompt omits the section"},
        {"kind": "raises", "type": "Exception (repository failure)", "handled_at": "ContextProvider.get_memory_context catches it, logs, and appends 'rules' to degraded_dimensions"},
        {"kind": "effect", "type": "may refresh rule state (expiry, promotion, demotion) as a side effect of list_active"}
      ]
    },
    {
      "site": "ContextProvider.get_memory_context",
      "callee": "ContextProvider.get_rules_context",
      "exits": [
        {"kind": "returns", "type": "MemoryContext with facts, personality, ambient and rules dimensions", "handled_at": "ContextBuilder.build assigns it to Context.memory"},
        {"kind": "absent", "type": "—", "handled_at": "impossible: the method always returns a MemoryContext, degrading dimensions individually"},
        {"kind": "raises", "type": "—", "handled_at": "cannot raise for a dimension failure: each try/except degrades that dimension instead; a failure outside the four blocks would propagate to AgentLoop"},
        {"kind": "effect", "type": "may load rules through RuleService.list_active"}
      ]
    },
    {
      "site": "Reasoner._build_prompt",
      "callee": "render_rules",
      "exits": [
        {"kind": "returns", "type": "list[ChatMessage] with an extra system message when rules exist", "handled_at": "Reasoner.reason / reason_stream pass it to LLMClient.chat / chat_stream"},
        {"kind": "absent", "type": "no rules section", "handled_at": "render_rules returns None for an empty list and Reasoner skips the message"},
        {"kind": "raises", "type": "—", "handled_at": "cannot raise: render_rules is a pure formatter over Rule dataclasses"},
        {"kind": "effect", "type": "pure"}
      ]
    },
    {
      "site": "rules_list",
      "callee": "RuleService.list_active",
      "exits": [
        {"kind": "returns", "type": "—", "handled_at": "prints one line per rule (id, state, confidence, condition → action) with typer.echo"},
        {"kind": "absent", "type": "—", "handled_at": "prints 'no rules' when the list is empty and exits 0"},
        {"kind": "raises", "type": "asyncpg.PostgresError, ValueError (bad --db-url)", "handled_at": "asyncio.run propagates to typer: non-zero exit with the message"},
        {"kind": "effect", "type": "read-only (list_active may refresh expired rules)"}
      ]
    },
    {
      "site": "rules_show",
      "callee": "RuleService.get",
      "exits": [
        {"kind": "returns", "type": "—", "handled_at": "prints fields, last 10 uses and superseded_by with typer.echo"},
        {"kind": "absent", "type": "None", "handled_at": "prints 'rule not found: <id>' and raises typer.Exit(1)"},
        {"kind": "raises", "type": "ValueError for a non-UUID id, asyncpg.PostgresError", "handled_at": "typer exits non-zero with the message"},
        {"kind": "effect", "type": "read-only"}
      ]
    },
    {
      "site": "rules_archive",
      "callee": "RuleService.archive",
      "exits": [
        {"kind": "returns", "type": "—", "handled_at": "prints the archived rule id and state"},
        {"kind": "absent", "type": "None", "handled_at": "prints 'rule not found: <id>' and raises typer.Exit(1)"},
        {"kind": "raises", "type": "ValueError for a non-UUID id, asyncpg.PostgresError", "handled_at": "typer exits non-zero with the message"},
        {"kind": "effect", "type": "writes state=archived"}
      ]
    }
  ]
}
```

## Test plan (the exits above are the list)

`tests/unit/rules/test_scoring.py` pins the spec's `## Invariants` table:
bounds, silence (neutral) moves nothing, an expired use contributes nothing,
recompute equals stored posterior, dictated rule starts ≥ 0.9 and does not
demote, correction-born rule stays staging, promotion needs 3 positives across
2 sessions, demotion < 0.7, archive < 0.3.

`tests/unit/rules/test_service.py` drives the lifecycle through
`InMemoryRuleRepository`: create dictated / from correction, record use,
promotion, demotion by expiry, archive, split redistribution (each use lands
with exactly one child, children disjoint by construction of the test data),
merge union and "definitive only if all parents were", render (staged marked,
definitive silent).

`tests/unit/rules/test_repository.py` mocks the session factory and asserts
the SQL shape and row mapping (repo convention: `AsyncMock` over `DbSession`).

`tests/memory/test_context_provider.py` gains the rules dimension and its
degradation. `tests/unit/agentic/` gains the prompt section assertion.
`tests/unit/rules/test_cli_rules.py` pins the three commands.
