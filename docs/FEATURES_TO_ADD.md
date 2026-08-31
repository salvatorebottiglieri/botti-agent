# Features to Add

> Exploratory ideas not yet on the implementation plan. Each entry captures the hypothesis, where it would slot into the existing architecture, and the reasoning. Promote to `IMPLEMENTATION_PLAN.md` once a decision is made.

---

## Reservoir Computing in the Learning Module

**Status:** ✅ promoted to **Wave 7.1** (2026-05-05). See [`docs/adr/0001-reservoir-computing-for-learning-module.md`](./adr/0001-reservoir-computing-for-learning-module.md).

**Hypothesis:** A small Echo State Network (ESN) / Liquid State Machine (LSM) inside the Learning Module is a good fit for processing the minion event streams, with a secondary use as a salience estimator on the event bus.

### Where it fits

The codebase has an event-bus architecture (the "river") fed by continuous, multimodal sensor streams from minions: `location`, `payment`, `activity`, `calendar`, `call_log`, `app_usage` (`docs/ARCHITECTURE.md:101-106`). The Learning Module is currently a stub (`docs/ARCHITECTURE.md:2054-2078`, no code under `src/cortex/learning/` yet) and is responsible for `PatternAnalyzer`, `PreferenceEngine`, `Recommender` — i.e. detecting **temporal / behavioral / spatial / financial / contextual** patterns over time (`docs/ARCHITECTURE.md:864-878`). That is exactly the task class reservoir computing targets.

There is also `EventMetadata.salience: float` already plumbed through the bus (`docs/ARCHITECTURE.md:87`) — a natural output for a small reservoir + linear readout.

### Why it makes sense here

1. **Streaming, online data.** Minion events arrive continuously and asynchronously. RC consumes streams natively — no replay buffers, no retraining of recurrent weights. The `aiomqtt` / event-bus model fits drop-in.
2. **Cheap compared to the LLM.** Memory and Learning both "own an LLM" (`docs/ARCHITECTURE.md:174, 855`), and the doc explicitly notes an LLM priority queue because calls are scarce (`Execution > Memory > Learning`, line 1818). A reservoir is matrix-vector ops on every tick — orders of magnitude cheaper. It can pre-filter / pre-score events so the LLM only fires on interesting windows.
3. **Multimodal numeric features.** `location` (lat/lon, dwell time), `payment` (amount, merchant category), `activity` (screen time, app category) are naturally vectorisable. A single reservoir can fuse them into one state vector that downstream readouts consume.
4. **Multiple readouts share one reservoir.** The module needs several heads — pattern detector, anomaly / novelty score, recommendation trigger, salience estimator. RC's "fixed reservoir + many cheap linear readouts" is exactly that shape; readouts can be trained independently as new pattern types appear.
5. **No symbolic conflict.** RC produces continuous signals (pattern probabilities, anomaly scores). They fit cleanly upstream of the existing symbolic layer: a high readout → emit `pattern.detected` → Memory's `FactExtractor` + `LogicEngine` (PyDatalog) handle the symbolic side. The reservoir handles "when something interesting happens"; the LLM / Datalog stack handles "what it means."
6. **Single-user, edge-friendly.** Cortex runs in Docker for one user, not a fleet. Tiny reservoirs (a few hundred neurons) are realistic on local hardware.

### Where it does NOT fit

- Agentic Loop reasoning, fact extraction, concept derivation — LLM-shaped, not RC-shaped.
- Fact recall / hierarchy promotion — embedding similarity + access-count weighting is already the right tool (`docs/ARCHITECTURE.md:238-247`).

### Concrete first step

`src/cortex/learning/reservoir.py` with:

- numeric feature encoder for minion events,
- ESN state update on each event,
- ridge-regression readouts producing:
  - `salience` for `EventMetadata`,
  - an anomaly score,
  - per-pattern probabilities that gate `pattern.detected` emission.

The reservoir itself stays fixed; readouts are trained offline from logged events.

---

## Bayesian Network for Context Inference

**Status:** Proposed (2026-05-06). Not yet promoted to a wave; no ADR yet.

**Hypothesis:** A small discrete Bayesian network sitting between the minion-derived evidence and the symbolic / LLM stack gives Cortex a fourth, complementary reasoning layer: calibrated probabilistic inference over discrete hypotheses with explicit dependencies. The strongest first use case is **sensor fusion into a posterior over latent user state** (working / commuting / resting / socialising / …), consumed by `MemoryService.get_context()` and the Recommender.

### Where it fits

The codebase already has three non-overlapping reasoning layers:

| Layer | Tool | Answers |
| --- | --- | --- |
| Continuous signal extraction | Reservoir / ESN (Wave 7.1, ADR-0001) | "When does something interesting happen?" |
| Symbolic consistency | PyDatalog Logic Engine (Memory) | "Is this derivation logically valid?" |
| Open-ended synthesis | LLM (Execution / Memory / Learning) | "What does it mean / what should we do?" |

The missing layer is **calibrated probabilistic inference over discrete hypotheses with explicit dependencies** — i.e. "given this evidence, what is P(hypothesis)?". Today this is done implicitly by the LLM (expensive, uncalibrated) or by ad-hoc updates to `Fact.confidence`. A small BN slots in cleanly: reservoir says *something happened*, BN says *what is most likely true given the evidence*, Datalog checks *no contradiction*, LLM verbalises.

The natural insertion point is `MemoryService.get_context()` — already declared as the entry point for ambient context in the agentic loop (see `MemoryService Interface` in `docs/ARCHITECTURE.md`). Today it returns latest fact lookups; with a BN it would return a joint posterior over latent context states.

### Why it makes sense here

1. **Sensor fusion is the textbook BN problem.** Minion streams (`location`, `payment`, `activity`, `calendar`, `call_log`, `app_usage` — see `docs/ARCHITECTURE.md` "Core Event Types") are exactly the multimodal noisy evidence BNs were designed to combine. Hand-crafting "user is at work" rules is brittle; a small DAG with online-updated CPTs is principled.
2. **Cheap, deterministic, edge-friendly.** ~6–10 discrete nodes → variable elimination is sub-millisecond, no GPU, no model server. Same posture as the reservoir choice (ADR-0001): matrix ops, not LLM calls. Does not compete for the `Execution > Memory > Learning` LLM priority queue.
3. **Calibrated `Fact.confidence`.** The fact model already carries `confidence: float ∈ [0,1]` and supports cascade invalidation. A BN provides a principled source for those numbers and for propagating them when a parent fact changes — replacing what is currently the LLM's informal job.
4. **Complements the existing stack, doesn't replace any of it.** Reservoir produces evidence signals (salience, anomaly, pattern probabilities) that become BN evidence nodes; PyDatalog still vetoes contradictions; the LLM still synthesises and verbalises. Each tool stays in its sweet spot.
5. **Single-user, edge-friendly (again).** Tiny networks fit Cortex's lean-dependency posture (asyncpg over an ORM, in-memory bus over Redis, raw migrations over Alembic). Either `pgmpy` or ~150 LoC of pure NumPy is enough for v1.
6. **Materialises as a Fact, not a parallel store.** Output is a `ContextPosterior` Pydantic model persisted as a `FactType.CONTEXT` with `confidence` and a `payload.distribution`. Discoverable, indexable, retractable — same lifecycle as every other fact.

### Where it does NOT fit

- **Agentic Loop reasoning** — that's the LLM. A BN cannot replace open-ended planning.
- **Memory recall / hierarchy promotion** — embedding similarity + access-count weighting is already the right tool (see `docs/ARCHITECTURE.md` "Recall Mechanism (Hybrid)").
- **Symbolic consistency checks** — that's PyDatalog. A BN does not reject contradictions, it just lowers their probability. The two stay side-by-side: derivations get both a logical proof *and* a posterior.
- **Replacing the reservoir.** Reservoir handles streaming continuous signals event-by-event; the BN consumes its (and other) outputs as evidence. The two are layered, not alternatives.

### Trade-offs / known risks

- **Structure is hand-crafted in v1.** A wrong DAG produces *confidently* incorrect posteriors — worse than the LLM's softer guesses. Mitigation: keep the network small (≤ 10 nodes), validate posteriors against periods with known labels (sleep, calendar-declared meetings), defer structure learning to a later wave.
- **Sparse data per user.** CPTs need samples. Mitigation: Dirichlet priors, online posterior updates, low-cardinality discrete variables, fall back to priors until `samples_seen ≥ N`.
- **Three weaker use cases to defer:** belief propagation across the whole fact graph (low ROI today; graph is small and Datalog + LLM cover it), recommendation gating `P(useful | state, history)` (needs feedback labels — sparse early on, fits Wave 7.x Recommender), and BN-on-top-of-reservoir-readouts (only pays off once multiple correlated readouts exist — defer to 7.3+).

### Concrete first step

Mirror the reservoir slice. Tentative package layout:

```
src/cortex/learning/bayes/
├── __init__.py
├── models.py        # BNStructure, CPT, ContextPosterior, EvidenceBundle
├── network.py       # discrete BN: nodes, edges, CPTs, variable-elimination inference
├── priors.py        # hand-defined priors per node (v1 structure lives in code)
├── updater.py       # Dirichlet online update from new facts / minion events
├── service.py       # ContextInferenceService.infer(evidence) -> ContextPosterior
├── persistence.py   # PostgresCPTRepository — load/save CPTs
└── cli.py           # python -m cortex.learning.bayes.cli infer-context
```

Initial DAG (illustrative; refined during implementation):

```
time_of_day  ──┐
weekday      ──┤
location_cluster ──► user_state ◄── calendar_evidence
activity_type ─┘                  ◄── recent_payment_category
```

`user_state ∈ {working, commuting, resting, socialising, sleeping}` — discrete, ≤ ~6 states.

Integration points:

- `MemoryService.get_context()` calls `ContextInferenceService.infer()` and includes the posterior in the returned context bundle.
- New low-salience event `context.inferred` (default salience ≈ 0.3) carries the posterior. Learning subscribes to update CPTs online; the reservoir's salience labeler may consume it as a feature.
- One migration: `bn_cpts (node, parent_state, distribution_jsonb, samples_seen, updated_at)`. DAG structure (the edges) lives in code for v1 — promotable to a row-based representation if structure ever needs to be edited at runtime.
- New optional runtime dependency: `pgmpy` (or zero deps if implemented in pure NumPy — both are viable, decide at ADR time).

CPTs initialised from priors; updated online with Dirichlet posteriors as facts accumulate. Structure stays fixed in v1; structure learning deferred until at least ~30 days of data are available.


# Neurosymbolic AI Patterns for Cortex

> Concrete neurosymbolic patterns for the Cortex architecture. Each entry captures
> the hypothesis, where it slots into the current codebase, pros and cons, and a
> concrete implementation sketch with file-level precision.
> Created: 2026-05-13

---

## 1. 🥇 LLM Proposes → Logic Disposes (Concept Validation Loop)

**Status:** Proposed (2026-05-13). No ADR yet.

**Hypothesis:** The LLM can generate derived concepts from facts, but it hallucinates. Adding a
PyDatalog logic engine as a validator — and routing rejected concepts back to the LLM with
the conflict reason — creates a self-correcting neurosymbolic loop. The LLM provides creative
synthesis (neural), the logic engine enforces consistency (symbolic).

### Where it fits

`MemoryService.build_concept()` in `src/cortex/services/memory_service.py` (line ~340)
already exists as a pure storage method: it receives pre-built concepts and stores them
with `validated=False`. There is no validation step today.

The Architecture doc (`docs/ARCHITECTURE.md`) plans a PyDatalog Logic Engine for "on-demand
validation — runs when a concept is proposed" and "cascade invalidation when mutable facts
change." But it doesn't describe the *feedback loop* — what happens when validation fails.

The insertion point is between `build_concept()` receiving a derivation and storing it. A new
`ConceptValidationService` sits in that gap.

### How the loop works

```
Facts ──► LLM proposes ConceptDerivation
              │
              ▼
   ┌──────────────────────────────┐
   │   ConceptValidationService   │
   │                              │
   │  1. Load source facts as       │
   │     Datalog axioms           │
   │  2. Parse proof_chain into     │
   │     Datalog assertions       │
   │  3. Query consistency          │
   │                              │
   │  valid? ──► store (validated) │
   │  invalid? ──► Rejection       │
   │              (conflicting,    │
   │               suggested_fix)  │
   └──────────────────────────────┘
              │
              ▼ (if rejected)
   LLM receives Rejection + source facts
   → revises proof_chain
   → retries (max 3)
```

### Why it makes sense here

1. **Concept storage is the exact chokepoint.** Every derived concept already passes
   through `build_concept()` — one insertion point validates everything.
2. **The logic engine is already planned.** PyDatalog is declared in the Architecture doc.
   This pattern just adds the feedback loop that makes it *neurosymbolic* rather than just
   a post-hoc filter.
3. **Cheap retries.** Most concepts will pass on the first try. The loop costs one extra
   LLM call *only* when the LLM made a logical error — which is exactly when you want to
   pay that cost.
4. **Rejections are training signal.** A `concept.rejected` event carries the conflicting
   facts and the reason. The Learning Module can use these to improve future LLM prompts
   or to learn which fact configurations confuse the model.
5. **No new infrastructure.** PyDatalog runs in-process. The loop is just async function
   calls — no queues, no new services.

### Pros

| Pro                                     | Detail                                                                                                          |
| --------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| **Prevents silent contamination** | An invalid concept stored today poisons all downstream reasoning. The loop catches it before storage.           |
| **Self-improving**                | Each rejection teaches the LLM what constraints matter. Future concepts in the same domain get better.          |
| **Traceable**                     | Every concept has a validated/invalidated lineage. You can audit*why* something was accepted or rejected.     |
| **Lean**                          | ~150 LoC for the validation service + ~100 LoC for the PyDatalog wrapper. No new dependencies beyond PyDatalog. |

### Cons

| Con                                               | Mitigation                                                                                                                                                        |
| ------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **PyDatalog learning curve**                | Wrap it behind a thin ~100 LoC `LogicEngine` class with a simple API: `validate(proof_chain, source_facts) → bool + errors`                                  |
| **LLM might loop forever**                  | Configurable max retries (3). After exhausting, store as `validated=False` with `Rejection` attached — still useful as "attempted but unprovable" knowledge. |
| **Symbolic repr must match Datalog syntax** | Enforce a canonical format:`predicate(subject, object)`. The LLM prompt includes examples. A `_parse_to_datalog()` helper normalises before validation.       |
| **Adds latency to concept storage**         | LLM retry is the only variable cost. First-pass validation is <1ms. Only ~10-20% of concepts will need retries.                                                   |

### Concrete implementation

**New files:**

```
src/cortex/memory/
├── logic_engine.py          # LogicEngine class wrapping PyDatalog (~100 LoC)
└── concept_validator.py     # ConceptValidationService (~150 LoC)
```

**`src/cortex/memory/logic_engine.py`** — thin PyDatalog wrapper:

```python
class LogicEngine:
    """Symbolic validation for concepts using PyDatalog."""

    def __init__(self):
        self._facts: dict[str, list] = {}       # predicate → list of (args...)
        self._immutable_facts: set[str] = set()  # facts that cannot be retracted

    def load_fact(self, fact: Fact) -> None:
        """Load a fact as a Datalog assertion."""
        pred, args = self._parse_symbolic(fact.symbolic_repr)
        # e.g., "works_at(user, office)" → +works_at('user', 'office')
        if fact.mutability == FactMutability.STATIC:
            self._immutable_facts.add(fact.symbolic_repr)
        ...

    def load_facts(self, facts: list[Fact]) -> None:
        """Batch load facts."""
        ...

    def validate(self, proof_chain: str) -> ValidationResult:
        """
        Parse the LLM's proof_chain as Datalog assertions and check consistency.

        Returns:
            ValidationResult(valid=True) or
            ValidationResult(valid=False, conflicting=[...], suggestion="...")
        """
        ...

    def _parse_symbolic(self, symbolic_repr: str) -> tuple[str, tuple]:
        """Parse 'predicate(arg1, arg2)' → ('predicate', ('arg1', 'arg2'))."""
        ...
```

**`src/cortex/memory/concept_validator.py`:**

```python
class ConceptValidationService:
    """
    Validates LLM-generated concepts against the fact base.

    On rejection, returns a Rejection with the conflicting facts and a
    suggested fix for the LLM to consume.
    """

    MAX_RETRIES = 3

    def __init__(self, logic_engine: LogicEngine, llm_client: LLMClient):
        self._logic = logic_engine
        self._llm = llm_client

    async def validate_concept(
        self,
        derivation: ConceptDerivation,
        source_facts: list[Fact],
    ) -> Concept | Rejection:
        """
        Validate a concept. If invalid, return Rejection.
        Caller retries with revised derivation up to MAX_RETRIES.
        """
        # Load source facts into fresh engine instance
        engine = LogicEngine()
        for f in source_facts:
            engine.load_fact(f)

        # Validate
        result = engine.validate(derivation.proof_chain)
        if result.valid:
            return Concept(..., validated=True)

        return Rejection(
            reason=result.reason,
            conflicting_facts=result.conflicting,
            suggested_fix=result.suggestion,
        )
```

**Changes to existing code:**

- **`MemoryService.build_concept()`** — insert call to `ConceptValidationService` before `self._concept_repo.store()`. Add `retries` parameter (default 3). On rejection, re-prompt LLM with the rejection context.
- **`MemoryService.__init__()`** — accept optional `concept_validator: ConceptValidationService`.
- **`src/cortex/main.py` (initialize_app)** — create `LogicEngine`, wire into `ConceptValidationService`, pass to `MemoryService`.
- **New event:** `concept.rejected` emitted when max retries exhausted. Learning Module subscribes.
- **New migration:** none needed — the existing `concepts` table already has `validated BOOLEAN`.

**Prompts:**

The LLM receives a concept-validation-specific system prompt:

```
You derived a concept with this proof chain: {proof_chain}

The logic engine rejected it because: {rejection.reason}
Conflicting facts: {rejection.conflicting_facts}

Revise your proof chain so it is logically consistent with the source facts.
Keep the same format: step-by-step reasoning, each step referencing specific facts.
```

---

## 2. 🥈 Datalog Rules as Symbolic Pattern Detectors

**Status:** Proposed (2026-05-13). No ADR yet.

**Hypothesis:** The reservoir (Wave 7.1) handles continuous salience and anomaly detection —
"when something interesting happens." Complement it with declarative Datalog rules that
detect *discrete symbolic patterns* across the fact graph — "what pattern did we find
and why." The reservoir provides statistical signal; the Datalog rules provide explainable,
auditable pattern detection with explicit provenance.

### Where it fits

The Learning Module (`src/cortex/learning/` — currently a stub) is responsible for
emitting `pattern.detected` events. Wave 7.1 plans a reservoir that outputs *probabilities*
(pattern scores, anomaly scores, salience). But a probability doesn't explain *what* the
pattern is or *which facts* prove it.

Datalog rules live in the Learning Module as a complementary detector. Each rule is a
declarative `.dl` file that queries the fact base. When a rule fires, it emits
`pattern.detected` with:

- the rule name (e.g., `"commute_pattern"`)
- the matched facts (full provenance chain)
- confidence derived from the matched facts' confidences

### Pattern catalogue (illustrative)

```prolog
# src/cortex/learning/rules/commute.dl

# User commutes between two locations on weekdays
commute_route(User, Home, Work) :-
    location(User, Home, Hour, Day),
    Hour < 9, Day in [mon,tue,wed,thu,fri],
    count(Location=Home, last_30d) >= 10,
    location(User, Work, Hour2, Day),
    Hour2 >= 9, Hour2 <= 17, Day in [mon,tue,wed,thu,fri],
    count(Location=Work, last_30d) >= 10,
    Home != Work.
```

```prolog
# src/cortex/learning/rules/subscription.dl

# User has a recurring payment → subscription
has_subscription(User, Merchant) :-
    payment(User, Amount1, Merchant, Date1),
    payment(User, Amount2, Merchant, Date2),
    abs_days(Date1, Date2) between (25, 35),   # ~monthly
    similarity(Amount1, Amount2) > 0.95,        # amounts match within 5%
    count(Merchant=Merchant, last_90d) >= 3.    # at least 3 occurrences
```

```prolog
# src/cortex/learning/rules/weekend_dining.dl

# User dines out on weekend evenings
weekend_diner(User) :-
    payment(User, _, _, "restaurant", Date),
    is_weekend(Date),
    hour(Date) >= 18, hour(Date) <= 22,
    count(restaurant_weekend, last_30d) >= 2.
```

### Why it makes sense here

1. **Explainability.** A reservoir readout says "pattern score = 0.87." A Datalog rule says
   "commute_route(user, home_addr, office_addr) because of facts #42, #87, #103." The
   latter is auditable and can be shown to the user.
2. **Declarative additivity.** Adding a new pattern type = adding a new `.dl` file. No
   code changes in the Learning Module. No retraining. The reservoir needs retraining for
   each new pattern head.
3. **Orthogonal to the reservoir.** The reservoir handles *when* things are interesting;
   Datalog handles *what* the patterns are. They can run independently, and their outputs
   can be fused (reservoir score × Datalog confidence = final pattern confidence).
4. **Leverages the fact base.** Every fact stored by `MemoryService` becomes queryable
   by Datalog rules. The richer the fact base grows, the more patterns become detectable
   — without changing any rule engine code.
5. **Rules can reference *derived* concepts.** A rule can build on other rules' outputs:
   `workaholic(User) :- commute_route(User, H, W), weekend_location(User, W).` This
   creates a hierarchy of patterns, each grounded in facts.

### Pros

| Pro                         | Detail                                                                                                         |
| --------------------------- | -------------------------------------------------------------------------------------------------------------- |
| **Explainable**       | Every pattern detection comes with a full provenance chain of facts.                                           |
| **Additive**          | New pattern = new file. No code changes, no retraining.                                                        |
| **Cheap to evaluate** | PyDatalog queries over the local fact set are sub-millisecond for hundreds of facts.                           |
| **Composable**        | Rules can reference other rules' outputs, building a pattern hierarchy.                                        |
| **User-editable**     | Technically, rules are just text files. A future "teach Cortex" feature could let users write or adjust rules. |

### Cons

| Con                                 | Mitigation                                                                                                                                                                      |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Hand-written rules**        | Rules are curated, not learned. But 15-20 well-written rules cover 80% of patterns. The reservoir + LLM handle the long tail.                                                   |
| **Rule conflicts**            | Two rules might fire for the same facts producing contradictory patterns. Solve with a priority/confidence tiebreaker in the rule engine.                                       |
| **PyDatalog syntax is niche** | Wrap in a thin `RuleEngine` class. Rules are written once, debugged once.                                                                                                     |
| **Needs periodic evaluation** | Evaluating all rules on every fact insert would be expensive. Schedule evaluation: (a) nightly batch, (b) when N new facts accumulate, (c) on-demand when LLM queries patterns. |

### Concrete implementation

**New files:**

```
src/cortex/learning/
├── rules/                    # Declarative pattern rules
│   ├── __init__.py
│   ├── commute.dl
│   ├── subscription.dl
│   ├── weekend_dining.dl
│   └── ...                   # more rules added over time
├── rule_engine.py            # RuleEngine: loads .dl files, evaluates against facts (~120 LoC)
└── pattern_emitter.py        # Converts rule matches → pattern.detected events (~80 LoC)
```

**`src/cortex/learning/rule_engine.py`:**

```python
class RuleEngine:
    """
    Evaluates Datalog rules against the fact base.

    Rules are loaded from .dl files in src/cortex/learning/rules/.
    Each rule file declares one or more pattern predicates.
    """

    def __init__(self, fact_repository: FactRepository):
        self._repo = fact_repository
        self._rules: dict[str, str] = {}  # rule_name → raw Datalog source
        self._load_rules()

    def _load_rules(self) -> None:
        """Load all .dl files from the rules directory."""
        rules_dir = Path(__file__).parent / "rules"
        for dl_file in rules_dir.glob("*.dl"):
            self._rules[dl_file.stem] = dl_file.read_text()

    async def evaluate_all(
        self,
        window_days: int = 30,
    ) -> list[PatternMatch]:
        """
        Evaluate all rules against facts from the last N days.

        Returns list of PatternMatch(name, confidence, source_facts, proof_trace).
        """
        facts = await self._repo.get_recent(limit=500)  # TODO: date filter
        engine = LogicEngine()
        for f in facts:
            engine.load_fact(f)

        matches = []
        for rule_name, rule_src in self._rules.items():
            for match in engine.query_rule(rule_src):
                confidence = self._compute_confidence(match.source_facts)
                matches.append(PatternMatch(
                    name=rule_name,
                    confidence=confidence,
                    source_facts=match.source_facts,
                    proof_trace=match.proof_trace,
                ))

        return matches

    def _compute_confidence(self, facts: list[Fact]) -> float:
        """Average confidence of matched facts, weighted by fact type."""
        ...
```

**`src/cortex/learning/pattern_emitter.py`:**

```python
class PatternEmitter:
    """Converts rule engine matches into pattern.detected events."""

    def __init__(self, event_bus: EventBus, rule_engine: RuleEngine):
        self._emitter = EventEmitter(event_bus, source_module="learning")
        self._engine = rule_engine

    async def scan_and_emit(self) -> list[str]:
        """Evaluate all rules, emit events for new patterns."""
        matches = await self._engine.evaluate_all()
        emitted = []
        for match in matches:
            if not self._is_duplicate(match):  # dedup against recently emitted
                await self._emitter.emit("pattern.detected", {
                    "pattern_name": match.name,
                    "confidence": match.confidence,
                    "source_fact_ids": [str(f.id) for f in match.source_facts],
                    "proof_trace": match.proof_trace,
                })
                emitted.append(match.name)
        return emitted
```

**Integration points:**

- **Schedule:** `PatternEmitter.scan_and_emit()` runs on a timer (nightly) or is triggered
  by a `fact.stored` event when N new facts accumulate.
- **Fusion with reservoir:** Reservoir's pattern readout produces a score `s_res ∈ [0,1]`.
  Datalog produces confidence `c_dl ∈ [0,1]`. Final confidence = `0.4·s_res + 0.6·c_dl`
  (Datalog weighted higher because it's grounded in actual facts).
- **No migration needed** — patterns are transient events consumed by Interaction and
  Execution modules. Persisting pattern history is deferred to a future wave.

---

## 3. 🥉 Constraint-Guarded Tool Selection

**Status:** Proposed (2026-05-13). No ADR yet.

**Hypothesis:** Before the LLM selects tools in the Agentic Loop, a lightweight symbolic
constraint layer filters the available tool set based on preconditions evaluated against
the current context. This prevents the LLM from selecting tools that would immediately
fail — reducing wasted iterations, LLM tokens, and user-facing errors.

### Where it fits

The Reasoner in `src/cortex/agentic/reasoner.py` (line ~60) receives `context.tools`
(the full list of available tool schemas) and passes them directly to the LLM via
`self._llm.chat(messages, tools=tools)`. Every tool is equally available regardless
of whether its preconditions are met.

The insertion point is between `ContextBuilder.build()` assembling tools and the
Reasoner receiving them. A `ConstraintGuard.filter_tools()` strips unsatisfiable tools
before the LLM ever sees them.

### How it works

```
ContextBuilder.build()
        │
        ▼
ToolRegistry.get_schemas() → [25 tools]
        │
        ▼
ConstraintGuard.filter_tools(tools, context) → [22 tools]
        │   (removes 3 tools whose preconditions aren't met)
        ▼
Reasoner.reason(context)  ← LLM only sees 22 tools
```

Each tool declares optional preconditions in its definition:

```python
class Tool(ABC):
    # ... existing fields ...

    @property
    def preconditions(self) -> list[ToolPrecondition]:
        """Preconditions that must be true for this tool to be usable."""
        return []
```

**Precondition examples:**

```python
# Shell tool: requires a valid working directory
ShellTool.preconditions = [
    ToolPrecondition(
        condition="filesystem.accessible",
        params={"path": "${working_dir}"},
        description="Working directory must exist and be accessible",
    )
]

# SSH tool (future): requires an active SSH connection
SSHTool.preconditions = [
    ToolPrecondition(
        condition="ssh.connected",
        params={"host": "${host}"},
        description="Must have an active SSH connection to the target host",
    )
]

# File read tool: requires the file to exist
FileReadTool.preconditions = [
    ToolPrecondition(
        condition="filesystem.exists",
        params={"path": "${path}"},
        description="File must exist",
        fallback_tool="file_search",  # LLM can use grep to find the file instead
    )
]
```

### Constraint checking is fast and deterministic

```python
class ConstraintGuard:
    """
    Filters tools based on preconditions evaluated against context.

    All checks are synchronous and O(1) to O(n) — no external calls.
    """

    def __init__(self, tool_registry: ToolRegistry):
        self._registry = tool_registry
        self._checkers: dict[str, callable] = {
            "filesystem.accessible": self._check_fs_accessible,
            "filesystem.exists": self._check_fs_exists,
            "session.authenticated": self._check_session,
        }

    def filter_tools(
        self,
        tools: list[ToolDefinition],
        context: Context,
    ) -> tuple[list[ToolDefinition], list[FilteredTool]]:
        """
        Return (passed_tools, filtered_tools).
        filtered_tools includes the reason and optional fallback tool.
        """
        passed = []
        filtered = []

        for tool_def in tools:
            tool = self._registry.get(tool_def.name)
            if tool is None:
                passed.append(tool_def)
                continue

            rejections = []
            for precond in tool.preconditions:
                checker = self._checkers.get(precond.condition)
                if checker and not checker(precond, context):
                    rejections.append(precond)

            if rejections:
                filtered.append(FilteredTool(
                    tool_name=tool_def.name,
                    failed_preconditions=rejections,
                    fallback_tool=self._resolve_fallback(rejections),
                ))
            else:
                passed.append(tool_def)

        return passed, filtered
```

### Why it makes sense here

1. **Injection point is already clean.** `ContextBuilder.build()` assembles tools →
   `Reasoner.reason()` receives them. Adding a filter between the two touches only one
   file (`context_builder.py`) and doesn't change the Reasoner's interface.
2. **Prevents entire classes of LLM errors.** The LLM cannot hallucinate a `shell` command
   with a non-existent working directory because the tool isn't offered. This saves wasted
   loop iterations (tool call → error → LLM retry).
3. **Preconditions are additive.** Adding a new tool with constraints = defining its
   `preconditions` property. No changes to the guard or Reasoner.
4. **Fallback hints.** When a tool is filtered, the guard can suggest a fallback tool.
   The LLM sees "ShellTool is unavailable (working dir missing). Try GrepTool instead."
   This is a compact signal that improves the LLM's decision without extra reasoning steps.
5. **Tiny.** ~50 LoC for the guard, ~20 LoC for `ToolPrecondition`, ~10 LoC per checker.
   No new dependencies.

### Pros

| Pro                                              | Detail                                                                       |
| ------------------------------------------------ | ---------------------------------------------------------------------------- |
| **Zero-cost filtering**                    | Checks are synchronous Python calls, no external I/O.                        |
| **Prevents hallucinated tool calls**       | LLM cannot call a tool that isn't in the list.                               |
| **Explains *why* a tool is unavailable** | The LLM gets context about what's missing, not just absence.                 |
| **Naturally extensible**                   | New tool = new preconditions. New checker = new entry in `_checkers` dict. |

### Cons

| Con                                                | Mitigation                                                                                                                                                                |
| -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Over-filtering could hide useful tools**   | Preconditions must be*necessary* conditions, not *nice-to-have*. Use judgment. Start conservative — only add preconditions to the 3-4 tools where failure is common. |
| **Checkers are hand-written**                | Each checker is ~10 LoC. For the initial set (filesystem, session), that's fine. For complex preconditions, defer to a future "dynamic precondition" system.              |
| **Context might not carry all needed state** | The guard takes `Context`, which already has session_id and memory info. If a checker needs more state, add it to `Context` — it's a lightweight dataclass.          |

### Concrete implementation

**New files:**

```
src/cortex/tools/
├── constraints.py            # ConstraintGuard, ToolPrecondition, FilteredTool (~80 LoC)
```

**Changes to existing files:**

- **`src/cortex/tools/interfaces.py`** — add `preconditions: list[ToolPrecondition]` property to `Tool` ABC (default `[]`).
- **`src/cortex/agentic/context_builder.py`** — insert `ConstraintGuard.filter_tools()` between tool assembly and `Context` construction. Add filtered tool info to context (so Reasoner can include it in the system prompt).
- **`src/cortex/agentic/models.py`** — add optional `filtered_tools: list[FilteredTool]` to `Context`.
- **`src/cortex/agentic/reasoner.py`** — if `context.filtered_tools` is non-empty, append a note to the system prompt:

```
Note: The following tools are currently unavailable:
- ShellTool: working directory not accessible
- SSHTool: no active SSH connection
Consider using fallback tools where suggested.
```

**`src/cortex/tools/constraints.py`:**

```python
@dataclass
class ToolPrecondition:
    condition: str              # Key into the checkers dict
    params: dict[str, str]      # Parameter values (${var} for context resolution)
    description: str            # Human-readable reason
    fallback_tool: str | None = None  # Suggested alternative tool

@dataclass
class FilteredTool:
    tool_name: str
    failed_preconditions: list[ToolPrecondition]
    fallback_tool: str | None = None


class ConstraintGuard:
    def __init__(self, tool_registry: ToolRegistry):
        self._registry = tool_registry
        self._checkers: dict[str, Callable[[ToolPrecondition, Context], bool]] = {
            "filesystem.accessible": self._check_fs_accessible,
            "filesystem.exists": self._check_fs_exists,
            "always": lambda p, c: True,   # Always passes — for testing
        }

    def filter_tools(
        self,
        tools: list[ToolDefinition],
        context: Context,
    ) -> tuple[list[ToolDefinition], list[FilteredTool]]:
        ...

    def _check_fs_accessible(
        self, precond: ToolPrecondition, context: Context
    ) -> bool:
        path = self._resolve_params(precond.params, context)
        return os.path.isdir(path.get("working_dir", "."))

    def _check_fs_exists(
        self, precond: ToolPrecondition, context: Context
    ) -> bool:
        path = self._resolve_params(precond.params, context)
        return os.path.isfile(path.get("path", ""))

    def _resolve_params(self, params: dict, context: Context) -> dict:
        """Resolve ${var} placeholders against context."""
        ...
```

---

## 4. Embedding + Symbolic Hybrid Fact Retrieval

**Status:** Proposed (2026-05-13). No ADR yet.

**Hypothesis:** The current `MemoryService._calculate_relevance_score()` uses text ILIKE
matching + confidence + recency boosts. Adding embedding-based semantic search (neural)
and filtering/ranking with symbolic constraints (type, confidence, Datalog closure) gives
a principled two-stage retrieval: semantic breadth from embeddings, logical coherence from
symbolic structure.

### Where it fits

`MemoryService._get_relevant_facts()` in `src/cortex/services/memory_service.py` (line ~120)
calls `self._fact_repo.search(query, ...)` which does SQL ILIKE against `symbolic_repr` and
`natural_lang_repr`. After retrieval, `_calculate_relevance_score()` boosts by recency,
session affinity, and access count.

The embedding insertion point is *before* the current text search — use embeddings for
initial candidate generation (semantic sweep), then apply existing symbolic boosts.
Additionally, a Datalog closure boost re-ranks facts that are connected via `source_facts`
edges (facts that prove or support each other get a coherence bonus).

### Two-stage retrieval

```
Query: "Where does the user usually work?"
        │
        ▼
Stage 1 — Embedding (neural):
    encode(query) → pgvector ANN search → top 50 candidates
    (semantically broad, catches "office" even if query says "work")
        │
        ▼
Stage 2 — Symbolic re-ranking:
    a) Filter by FactType (if specified)
    b) Filter by min confidence
    c) Datalog closure boost:
       - For each candidate fact F, find other facts that share
         source_fact edges with F.
       - If F is part of a coherent cluster (e.g., 3+ connected facts),
         boost F's score.
    d) Apply existing boosts (recency, session, access_count)
        │
        ▼
    Return top 10 ranked facts
```

### Datalog closure boost (symbolic coherence)

```python
async def _symbolic_coherence_boost(
    self,
    candidates: list[Fact],
    all_facts: list[Fact],
) -> dict[UUID, float]:
    """
    Boost facts that are well-connected in the fact graph.

    For each candidate, count how many other candidates share
    source_fact edges with it. Facts in dense clusters are more
    likely to be mutually reinforcing knowledge.
    """
    boosts = {}
    # Build adjacency: fact_id → set of related fact_ids
    adjacency = defaultdict(set)
    for f in all_facts:
        if hasattr(f, 'source_facts'):
            for sf_id in f.source_facts:
                adjacency[f.id].add(sf_id)
                adjacency[sf_id].add(f.id)

    for fact in candidates:
        neighbors = adjacency.get(fact.id, set())
        candidate_ids = {c.id for c in candidates}
        overlap = len(neighbors & candidate_ids)
        boosts[fact.id] = min(0.3, overlap * 0.1)  # Cap at 0.3 boost

    return boosts
```

### Why it makes sense here

1. **Semantic search fills the vocabulary gap.** A user asks "where do I work?" but the
   fact says `location.office`. ILIKE on "work" won't match "office." Embeddings will.
2. **Symbolic re-ranking prevents semantic drift.** Embeddings alone might return facts
   about "remote work tools" when the user means "workplace location." Symbolic filters
   (type=LOCATION, confidence>0.6) anchor the results.
3. **Datalog closure rewards knowledge coherence.** Two isolated facts with high
   embedding similarity might both be relevant. But three *connected* facts that prove
   each other are almost certainly the right answer.
4. **pgvector is already in the Docker stack.** Postgres in `docker-compose.yml` can
   have `pgvector` enabled with one line. No new service, no new container.
5. **Backward compatible.** If embeddings aren't available (e.g., pgvector not enabled),
   fall back to the current ILIKE search. Graceful degradation.

### Pros

| Pro                                      | Detail                                                                                      |
| ---------------------------------------- | ------------------------------------------------------------------------------------------- |
| **Handles vocabulary mismatch**    | Embeddings catch synonyms and paraphrases that text search misses.                          |
| **Principled two-stage retrieval** | Common in production RAG systems. Embeddings for recall, symbolic for precision.            |
| **Coherence-aware ranking**        | Facts that "hang together" logically get ranked higher than isolated matches.               |
| **No new service**                 | pgvector is a Postgres extension. Embedding model is the existing LLM's embedding endpoint. |
| **Incremental**                    | Can ship Stage 1 (embeddings) first, add Stage 2 (symbolic re-ranking) later.               |

### Cons

| Con                                    | Mitigation                                                                                                                                                 |
| -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Embedding column + ANN index** | Add `embedding vector(1536)` to the `facts` table. Generate on `store_fact()`. pgvector handles the index.                                           |
| **Embedding cost**               | One `text-embedding-3-small` call per new fact (or batch them). For ~100 facts/day, that's negligible.                                                   |
| **Cold start**                   | If the embedding model is unavailable, fall back to ILIKE. The `MemoryService` already handles degraded dimensions gracefully (`degraded_dimensions`). |
| **Datalog closure adds latency** | Only compute for the top 50 candidates, not all facts. O(50²) pair check is <1ms.                                                                         |

### Concrete implementation

**Migration:**

```sql
-- migrations/006_fact_embeddings.sql
CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE facts ADD COLUMN embedding vector(1536);

CREATE INDEX idx_facts_embedding
  ON facts USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);
```

**Changes to existing files:**

- **`src/cortex/memory/models.py`** — add `embedding: list[float] | None = None` to `Fact`.
- **`src/cortex/memory/repository.py` (`PostgresFactRepository`)** — add `search_by_embedding(embedding, limit)` method using pgvector `<=>` operator. Modify `store()` to insert embedding.
- **`src/cortex/services/memory_service.py`** — modify `_get_relevant_facts()` to use two-stage retrieval. Add `_symbolic_coherence_boost()` for Datalog closure re-ranking. Modify `store_fact()` to generate embedding before storage.
- **`src/cortex/main.py`** — if pgvector extension is available, wire embedding generation into `MemoryService`.

**New dependencies:** `openai` embeddings endpoint (already available via `LLMClient` — add an `embed()` method to the base class).

**`LLMClient.embed()` addition to `src/cortex/llm/base.py`:**

```python
class LLMClient(ABC):
    # ... existing methods ...

    @abstractmethod
    async def embed(self, text: str | list[str]) -> list[list[float]]:
        """Generate embeddings for one or more texts."""
        ...
```

**`src/cortex/llm/providers/openai.py`:**

```python
async def embed(self, text: str | list[str]) -> list[list[float]]:
    texts = [text] if isinstance(text, str) else text
    resp = await self._client.embeddings.create(
        model="text-embedding-3-small",
        input=texts,
    )
    return [d.embedding for d in resp.data]
```

---

## 5. Abductive Reasoning for Context Inference

**Status:** Proposed (2026-05-13). No ADR yet.

**Hypothesis:** Abduction — "given observed facts, what missing facts would best explain
them?" — is the logical complement to the Bayesian Network proposal. The BN gives
P(latent | evidence). Abduction gives *candidate explanations* — concrete hypotheses
about *what might be true that hasn't been observed yet.* The LLM generates the hypotheses
(neural). Datalog validates that each hypothesis, if true, would indeed entail the
observation (symbolic). Valid hypotheses are stored as low-confidence concepts, awaiting
confirmation from future evidence.

### Where it fits

Between the reservoir's `"something just happened"` signal and the Bayesian Network's
`"what state is most likely"` inference. Abduction fills the explanatory gap:

```
Reservoir:  "salience spike at 14:32 on Tuesday"
Bayes Net:  "P(working)=0.72, P(socialising)=0.03, ..."
Abduction:  "IF user is sick THEN location=home at 14:32 on Tuesday makes sense"
            "IF user is working-from-home THEN location=home at 14:32 on Tuesday makes sense"
```

The abduced hypotheses are stored as `Concept(derivation_method="abduction", confidence=0.4)`
— low confidence because they're guesses, but explicit and testable. Later evidence
(e.g., a calendar event saying "WFH," or a chat message "I'm not feeling well") confirms
or refutes them → confidence updated.

### How the loop works

```
Minion event (e.g., location=home, time=Tuesday 14:32, salience=0.82)
        │
        ▼
AbductionTrigger: salience > threshold → trigger abduction
        │
        ▼
LLM (prompted with: observed facts + recent history):
    "Given these observations, propose 2-3 hypotheses about
     what might be going on. Format: predicate(subject, object)."
        │
        ├── "user_is_sick(user)"
        ├── "working_from_home(user)"
        └── "home_maintenance(user)"
        │
        ▼
Datalog Validation:
    "For each hypothesis H: if H were true + known facts,
     does the observation logically follow?"
        │
        ├── "user_is_sick" → entails location=home ✓
        ├── "working_from_home" → entails location=home ✓ (if known: user has WFH setup)
        └── "home_maintenance" → does NOT entail location=home alone ✗ (rejected)
        │
        ▼
Store valid hypotheses as low-confidence Concepts:
    - user_is_sick(user), confidence=0.35
    - working_from_home(user), confidence=0.45 (boosted by WFH fact)
        │
        ▼
Later: user sends chat message "I'm staying home today, feeling sick"
        │
        ▼
Confirmation: MemoryService boosts confidence of user_is_sick → 0.85
```

### Why it makes sense here

1. **Explains the unexplained.** The reservoir says "something's off." The Bayes Net says
   "probably working." Abduction says "but maybe they're sick, or maybe it's a holiday."
   This is the creative, explanatory reasoning that neither statistics nor logic alone
   provides.
2. **Hypotheses become testable facts.** Every abduced concept has `validated=False` and
   low confidence. Future evidence that aligns → confidence boost. Evidence that
   contradicts → retracted. This creates a self-correcting knowledge loop.
3. **Natural integration with the Bayesian Network.** Abduction proposes candidate latent
   variables. The BN already has `user_state` as a latent node. Abduction can *suggest new
   states* for that node — e.g., "sick" isn't in the initial BN structure, but after
   abduction proposes it and confirmation happens, the BN can add it as a new state.
4. **LLM is good at this.** "What might explain this observation?" is a creative
   reasoning task the LLM excels at. Datalog then filters the creative ideas for
   logical coherence — exactly the neurosymbolic division of labor.
5. **Gated by salience.** Abduction only triggers when the reservoir says something
   interesting happened (salience > threshold). This prevents the LLM from being called
   on every mundane event.

### Pros

| Pro                                     | Detail                                                                                                                                                |
| --------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Generates testable hypotheses** | Every abduction is a concrete, retractable concept. Not vague LLM rambling.                                                                           |
| **Self-correcting**               | Future evidence confirms or refutes hypotheses. Confidence moves over time.                                                                           |
| **Complements the BN**            | BN handles P(state                                                                                                                                    |
| **Gated by salience**             | Only triggers on interesting events, conserving LLM quota.                                                                                            |
| **Explainable to the user**       | "I noticed you're home on a Tuesday afternoon. Are you working from home or feeling unwell?" — the abduction directly informs proactive interaction. |

### Cons

| Con                                                      | Mitigation                                                                                                                                                                            |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **LLM generates spurious hypotheses**              | Datalog validation filters them. Max 3 hypotheses per trigger. Low initial confidence (0.3-0.5) prevents over-reliance.                                                               |
| **Confirmation might take days**                   | That's fine. Low-confidence concepts just sit in the DB. They don't affect reasoning until confirmed.                                                                                 |
| **Adds cost (LLM call per salient event)**         | Salience threshold ensures this only fires 2-5 times per day. Each call is a short prompt (~200 tokens). Cost is negligible.                                                          |
| **Datalog must parse natural language hypotheses** | Force the LLM to output in symbolic format:`predicate(subject, object)`. The prompt includes examples. A `_normalize_hypothesis()` helper cleans up common LLM formatting quirks. |

### Concrete implementation

**New files:**

```
src/cortex/memory/
├── abducer.py               # AbductionService (~200 LoC)
```

**`src/cortex/memory/abducer.py`:**

```python
class AbductionService:
    """
    Generates and validates explanatory hypotheses for anomalous events.

    Triggered by high-salience minion events. LLM proposes hypotheses;
    Datalog validates them. Valid hypotheses are stored as low-confidence
    Concepts and emitted as events.
    """

    SALIENCE_THRESHOLD = 0.75      # Only trigger on events this salient
    MAX_HYPOTHESES = 3             # Max hypotheses per trigger
    DEFAULT_CONFIDENCE = 0.35       # Initial confidence for abduced concepts
    COOLDOWN_SECONDS = 300         # Min time between triggers

    def __init__(
        self,
        llm_client: LLMClient,
        logic_engine: LogicEngine,
        memory_service: MemoryService,
        event_bus: EventBus,
    ):
        ...

    async def maybe_abduce(
        self,
        event: MinionEvent | BaseEvent,
        salience: float,
    ) -> list[Concept]:
        """
        If salience > threshold and cooldown has passed, generate hypotheses.

        Returns list of stored Concepts (may be empty if no hypotheses validated).
        """
        if salience < self.SALIENCE_THRESHOLD:
            return []
        if not self._cooldown_ok():
            return []

        # 1. Get recent context
        recent_facts = await self._memory.get_recent_facts(limit=20)
        context_str = self._build_context(event, recent_facts)

        # 2. Ask LLM for hypotheses
        hypotheses = await self._llm_generate_hypotheses(context_str)

        # 3. Validate each with Datalog
        valid = []
        for hyp in hypotheses:
            if self._logic.validate_hypothesis(hyp, recent_facts):
                concept = await self._memory.build_concept(
                    symbolic_repr=hyp.symbolic_repr,
                    natural_lang_repr=hyp.natural_lang_repr,
                    source_facts=recent_facts,
                    derivation_method="abduction",
                    proof_chain=hyp.proof_chain,
                )
                concept.confidence = self.DEFAULT_CONFIDENCE
                concept.validated = False  # Awaiting confirmation
                valid.append(concept)

        # 4. Emit event
        if valid:
            await self._emitter.emit("concept.abduced", {
                "concept_ids": [str(c.id) for c in valid],
                "trigger_event_type": getattr(event, 'type', 'unknown'),
                "salience": salience,
            })

        return valid

    async def confirm_hypothesis(
        self,
        concept_id: UUID,
        supporting_facts: list[Fact],
    ) -> Concept:
        """
        Called when new evidence supports an abduced hypothesis.
        Boosts confidence and marks as validated.
        """
        concept = await self._memory.get_concept(concept_id)
        if concept is None or concept.derivation_method != "abduction":
            raise ValueError(f"Not an abduced concept: {concept_id}")

        new_confidence = min(0.9, concept.confidence + 0.25)
        concept.confidence = new_confidence
        concept.validated = True
        await self._memory.update_concept(concept)
        return concept

    async def refute_hypothesis(
        self,
        concept_id: UUID,
        reason: str,
    ) -> None:
        """Called when new evidence contradicts an abduced hypothesis."""
        await self._memory.retract_fact(concept_id, reason=f"Abduction refuted: {reason}")
```

**Integration points:**

- **`src/cortex/services/minion_service.py`** — after `MemoryService.handle_event()`,
  check salience. If high, call `AbductionService.maybe_abduce()`.
- **`src/cortex/services/memory_service.py`** — when processing a `user.message` event,
  scan recent abduced concepts for alignment. If the user says something that matches
  a hypothesis → `AbductionService.confirm_hypothesis()`.
- **`src/cortex/main.py`** — wire `AbductionService` with `LogicEngine`, `MemoryService`,
  `LLMClient`, `EventBus`.
- **New event:** `concept.abduced` — emitted when abduction produces valid hypotheses.
  Learning Module subscribes to build training data for better abduction prompts.
- **No migration needed** — abduced concepts use the existing `concepts` table.
  `derivation_method="abduction"` distinguishes them.

**LLM prompt for abduction:**

```
You are an abductive reasoning engine for Cortex.

OBSERVED EVENT:
{event_description}

RECENT FACTS (last 24 hours):
{recent_facts}

CURRENT TIME: {time}
DAY OF WEEK: {day}

TASK: Propose up to 3 hypotheses that explain this observation.
Each hypothesis should be a fact that, if true, would make the
observation expected rather than anomalous.

OUTPUT FORMAT (JSON):
[
  {
    "symbolic_repr": "predicate(subject, object)",
    "natural_lang_repr": "The user is ...",
    "proof_chain": "If H is true, then observation O follows because ..."
  }
]

Keep hypotheses concrete and testable. Avoid vague explanations.
```

---

## 📊 Summary: Implementation Priority

| # | Pattern                                  | Where                       | Effort                        | Dependencies          | Value                            |
| - | ---------------------------------------- | --------------------------- | ----------------------------- | --------------------- | -------------------------------- |
| 3 | **Constraint-Guarded Tools**       | `tools/`, `reasoner.py` | Tiny (~80 LoC)                | None                  | Prevents tool errors immediately |
| 1 | **LLM → Logic Validate**          | `memory/`                 | Small (~250 LoC)              | PyDatalog             | Core neurosymbolic loop          |
| 4 | **Embedding + Symbolic Retrieval** | `memory/`, DB             | Medium (~300 LoC + migration) | pgvector, LLM embed() | Better fact retrieval            |
| 2 | **Datalog Rules for Patterns**     | `learning/`               | Medium (~250 LoC)             | PyDatalog, Wave 7.1   | Explainable patterns             |
| 5 | **Abductive Reasoning**            | `memory/`                 | Medium (~300 LoC)             | PyDatalog, reservoir  | Creative explanation             |

**Recommended order: 3 → 1 → 4 → 2 → 5**

Pattern 3 is trivial and pays off immediately by preventing LLM tool call errors.
Pattern 1 is the foundational neurosymbolic loop — it makes all LLM-generated knowledge
trustworthy. Pattern 4 improves the quality of facts fed into every reasoning step.
Patterns 2 and 5 build on the Logic Engine and reservoir once those foundations exist.

---

## 🔗 How These Patterns Compose

```
                          ┌─────────────────────────────────┐
                          │         MINION EVENTS           │
                          └───────────────┬─────────────────┘
                                          │
                    ┌─────────────────────┼─────────────────────┐
                    │                     │                     │
                    ▼                     ▼                     ▼
           ┌──────────────┐    ┌──────────────────┐   ┌────────────────┐
           │  Reservoir   │    │  Datalog Rules   │   │  Bayes Net     │
           │  (Wave 7.1)  │    │  (Pattern #2)    │   │  (Proposed)    │
           │              │    │                  │   │                │
           │ "salience=0.8│    │ "commute detected│   │ "P(working)=.72│
           └──────┬───────┘    └────────┬─────────┘   └───────┬────────┘
                  │                     │                      │
                  │    ┌────────────────┼──────────────────────┘
                  │    │                │
                  ▼    ▼                ▼
           ┌──────────────────────────────────────┐
           │        Abduction (Pattern #5)         │
           │                                      │
           │  "Might be sick, or working from     │
           │   home. Storing as low-conf concept." │
           └──────────────────┬───────────────────┘
                              │
                              ▼
           ┌──────────────────────────────────────┐
           │     LLM Proposes → Logic Disposes     │
           │            (Pattern #1)              │
           │                                      │
           │  "Is this abduced concept logically   │
           │   consistent with known facts?"       │
           │                                      │
           │  valid → store    invalid → revise    │
           └──────────────────┬───────────────────┘
                              │
                              ▼
           ┌──────────────────────────────────────┐
           │       MemoryService.get_context()     │
           │                                      │
           │  Embedding search → Symbolic filter  │
           │            (Pattern #4)              │
           │                                      │
           │  Returns: facts + personality +       │
           │  ambient + abduced + BN posterior     │
           └──────────────────┬───────────────────┘
                              │
                              ▼
           ┌──────────────────────────────────────┐
           │         Agentic Loop                 │
           │                                      │
           │  Constraint-Guarded Tools (#3)       │
           │  LLM reasoning with rich context     │
           │  → Response or action                │
           └──────────────────────────────────────┘
```

---

*Last updated: 2026-05-13*