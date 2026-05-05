# Reservoir computing for the Learning Module

The Learning Module needs to extract patterns and salience from continuous, multimodal minion event streams (`location`, `payment`, `activity`, `app_usage`, …). We chose an Echo State Network — fixed random reservoir + trained linear readouts, in pure NumPy — over the alternatives: LLM-based labeling, sklearn classifiers, or a pure heuristic with no learning component.

Reasons:

- **Streaming-native.** No replay buffers, no recurrent training. Each event advances reservoir state in O(1) — fits the asyncio event-bus model.
- **Cheap relative to the LLM.** Matrix-vector ops per event versus an LLM call. Cortex already runs three LLM owners on a priority queue (`Execution > Memory > Learning`, `docs/ARCHITECTURE.md:1817-1818`); the reservoir lets Learning act on every event without competing for that quota.
- **One reservoir, many readouts.** Salience (Wave 7.1), anomaly (7.2), and pattern probabilities (7.3) all share the same reservoir state. Per-target sklearn classifiers wouldn't share state, and would each need their own feature engineering.
- **Deterministic, edge-friendly.** Single user, single container, ~500 reservoir neurons, no GPU, no model server. Matches the codebase's posture on lean dependencies (asyncpg over an ORM, in-memory event bus over Redis, raw migrations over Alembic).

Trade-off: the Learning Module won't capture deep semantic novelty the way an LLM-driven labeler would. We accept that for cost and latency, and leave the door open for hybrid LLM-labeled training data later — the ridge readout's input shape is independent of how labels are produced, so swapping the labeler is a one-file change.
