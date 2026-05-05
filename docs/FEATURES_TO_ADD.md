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
