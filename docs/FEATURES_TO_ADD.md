# Features to Add

> Exploratory ideas not yet on the implementation plan. Each entry captures the hypothesis, where it would slot into the existing architecture, and the reasoning. Promote to `IMPLEMENTATION_PLAN.md` once a decision is made.

---

## Reservoir Computing in the Learning Module

**Status:** ✅ promoted to **Wave 7.1** (2026-05-05). See [`IMPLEMENTATION_PLAN.md`](./IMPLEMENTATION_PLAN.md) and [`docs/adr/0001-reservoir-computing-for-learning-module.md`](./adr/0001-reservoir-computing-for-learning-module.md).

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
