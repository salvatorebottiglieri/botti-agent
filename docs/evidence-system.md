# Evidence System

> Single source of truth for the evidence-based fact confidence system.
> **Update this document whenever the evidence system changes.**
> Rationale: `docs/adr/0013-evidence-based-fact-confidence.md`. Vocabulary: `CONTEXT.md` (`## Memory`).
> Work items: issues #82 (conversation extraction + STATIC producer) and #83 (evidence engine).

## Purpose

A Fact's confidence is the posterior probability that its current value is true, computed
over accumulated Evidence. The more recent and trustworthy the supporting evidence, the
higher the confidence. Irrefutable facts (axioms) are Static Facts, pinned at 1.0 and
outside the evidence machinery entirely.

## Concepts

Defined in the `CONTEXT.md` glossary (`## Memory`): **Fact**, **Confidence**, **Evidence**,
**EvidenceStore**, **Likelihood ratio (LR)**, **Static Fact**. Use those terms; don't drift
to synonyms the glossary avoids.

## Data model

- `facts` (existing): `confidence` is now the posterior over *active* evidence, kept fresh
  by ingestion and the sweep.
- `evidence` (new): one row per accepted observation —
  `fact_id` (FK), `source_type` (`sensor` | `user_confirm` | `llm`), `source_id`,
  `value_hash` (the value the observation supported), `strength` (the ingested fact's
  confidence), `observed_at`. Indexed on `(fact_id, observed_at)` and `(observed_at)`.

## Ingestion flow

Every observation (sensory event today; conversation message after #82) reaches the fact
store through the same seam: `FactStore.add_fact` → `EvidenceStore.record(fact)`:

1. **Dedup**: if an evidence with the same `(source_type, source_id, fact_id, value_hash)`
   exists within the last hour, the observation is discarded — it does not count again.
2. **New fact**: stored with prior confidence 0.5, then the evidence is applied (uniform
   pipeline — the first observation uses the same rule as later ones).
3. **Value change**: the fact's value is replaced (existing dedup-on-write); old evidence is
   *not* deleted but stops contributing because its `value_hash` no longer matches. Reverting
   the value restores the old evidence.
4. **Recompute**: the posterior over active evidence replaces `facts.confidence`.
5. **Static Fact**: skip everything — no evidence recorded, confidence stays 1.0, retraction
   refused.

## Update rule

```
logit(p') = logit(p) + (2·strength − 1) · ln(LR_base(source_type))
```

- `logit(p) = ln(p / (1−p))`; O(1) per evidence; deterministic.
- `strength < 0.5` weakens (evidence against), `strength = 0.5` is neutral, `strength = 1`
  applies the full LR; interpolation happens in log space (linear interpolation goes
  negative below 0.5 — see ADR-0013).
- Confidence saturates toward 1.0 as supporting evidence accumulates.

### Calibration constants (the only magic numbers — isolated and documented)

| Constant | Value |
|---|---|
| `LR(user_confirm)` | 20 |
| `LR(sensor)` | 10 |
| `LR(llm)` | 3 |
| prior (new fact) | 0.5 |
| dedup window | 1 h per `(source_type, source_id, fact_id, value_hash)` |
| window `EPHEMERAL` | 1 day |
| window `MUTABLE` | 6 months |
| window `SEMI_STATIC` | 5 years |
| window `STATIC` | none (no accumulation) |
| sweep interval | hourly (default) |

The constants are the calibration point for the future Bayesian network proposed in
`docs/FEATURES_TO_ADD.md` — change them only with that path in mind.

## Evidence lifecycle

- **Dedup**: identical observations from the same source within an hour count once.
- **Value-tagging**: an evidence is *active* iff `observed_at >= now − window(mutability)`
  AND `value_hash` matches the fact's current value. Active evidence is the whole story:
  this is what makes the system robust to lies and relapses ("I quit coffee" is an
  intention, not proof that "I don't like coffee").
- **Sweep**: a periodic asyncio task recomputes `facts.confidence` for facts whose active
  evidence expired. It exists because the stored confidence must keep matching the
  posterior — otherwise stale evidence would inflate confidence forever (and the SQL
  `ORDER BY confidence` retrieval path would rank on stale numbers).

## Static Facts

Confidence pinned at 1.0, never record evidence, never overwritten, never retracted.
Producer: the LLM extraction prompt marks irrefutable user statements as static (#82 —
also wires conversation extraction and separates attitudes from behaviors as distinct
facts).

## Invariants

| Law | Negation → test |
|---|---|
| Stored confidence equals the posterior over active evidence | recompute from `evidence` and compare, after sweep |
| Static Facts: confidence 1.0, zero evidence rows, never retracted | `SELECT` over static facts |
| No evidence contributes after its window expires | posterior over active evidence ignores it |
| No evidence with a non-matching `value_hash` contributes | posterior unchanged after value change |
| At most one evidence per `(source, source_id, fact, value)` per hour | count within window |
| Confidence always in [0, 1] | out-of-range assertion |

## Out of scope

- **Cross-fact contradictions** (same predicate, different `symbolic_repr` coexisting) —
  LogicEngine territory, issue #27.
- **Retirement of superseded facts** (stale facts with different `symbolic_repr` lingering
  in search).
- **"Why does Cortex believe X?" UX** — enabled by the evidence table, separate ticket.
- **Backfill** of existing facts (they keep their confidence, no evidence rows).
- **Event-bus events** on confidence updates.

## Sources of truth

- `docs/adr/0013-evidence-based-fact-confidence.md` — why (rejected alternatives,
  rationale).
- Issues #82, #83 — the work, with acceptance criteria.
- `CONTEXT.md` — vocabulary.
- This document — the system as a whole. **Keep it in sync with changes.**
