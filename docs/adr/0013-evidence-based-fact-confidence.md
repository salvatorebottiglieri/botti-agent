# Evidence-based fact confidence

Cortex stored a fixed `confidence` on each fact: sensory events got hardcoded values
(0.8–0.9), LLM extraction judged its own, and a repeated observation of the same fact
overwrote the stored value (last-writer-wins) instead of corroborating it. Evidence never
aged out and there was no provenance for "why does Cortex believe this?".

We replaced this with a Bayesian evidence engine: every accepted observation of a fact is
recorded as an Evidence row (source type, source id, strength, observed value, timestamp),
and a fact's confidence is the posterior probability that its current value is true,
computed over **active** evidence — evidence within the fact's mutability window whose
observed value matches the current value. The update runs in logit space:

```
logit(p') = logit(p) + (2·strength − 1) · ln(LR_base(source_type))
```

with calibration constants `LR(user_confirm)=20`, `LR(sensor)=10`, `LR(llm)=3` and prior
0.5 for every new fact. Evidence is deduplicated per `(source_type, source_id, fact_id,
value_hash)` within 1 hour. The evidence window is per mutability: `EPHEMERAL=1 day`,
`MUTABLE=6 months`, `SEMI_STATIC=5 years`, `STATIC=none`. STATIC facts are pinned at
confidence 1.0, never record evidence, and cannot be retracted.

Value-tagged evidence replaced a blunt "reset on value change" rule: when a fact's value
changes, old evidence stops counting (it recorded a different value) but is not deleted —
reverting the value restores it. This keeps the system robust to lies and relapses ("I quit
coffee" is an intention, not proof that "I don't like coffee"; and liking coffee ≠ drinking
coffee are distinct facts).

A new `EvidenceStore` component owns the dedup, the update, and a periodic asyncio sweep
that recomputes stored confidence for facts whose active evidence expired, keeping the
stored confidence equal to the posterior over active evidence.

Rejected alternatives:

- **Reset-on-value-change** — destroys valid history and conflates a stated intention with
  a changed truth (people lie, even to themselves).
- **Running mean of observation confidences** — non-monotonic: more evidence doesn't imply
  more certainty, which is the whole point of the system.
- **Count-curve `conf = 1 − (1−p₀)·αⁿ`** — cannot distinguish source trustworthiness or
  weakening evidence.
- **Fixed LR per source, ignoring the extractor's confidence** — makes the LLM-judged
  confidence meaningless.
- **Lazy recompute at read** — breaks the SQL `ORDER BY confidence` retrieval path.
- **Linear interpolation of LR** — invalid below strength 0.5 (produces negative LR);
  inversion must happen in log space.

Cross-fact contradictions (same predicate, different `symbolic_repr`) remain out of scope —
LogicEngine territory, ADR-0007 (issue #27). The producer for STATIC facts (an LLM
extraction flag, wired together with conversation extraction) is tracked as issue #82 and
blocks the evidence engine, issue #83. The LR and window constants are the only magic
numbers in the system; they are isolated and documented as the calibration point for the
future Bayesian network proposed in `docs/FEATURES_TO_ADD.md`.
