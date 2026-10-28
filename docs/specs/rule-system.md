# Rule System

> Single source of truth for the learned-rule system.
> **Update this document whenever the rule system changes.**
> Rationale: `docs/adr/0020-learned-rules.md`. Vocabulary: `CONTEXT.md` (`## Rules`).
> Work items: issue #130 (rule system v1).
>
> **Status: designed, not yet implemented.** There is no `rules` table in
> `migrations/`, no Rule model in `src/cortex/`, and the feedback classifier
> does not exist. This document is the implementation contract.

## Purpose

A Rule is a prescription Cortex applies when a condition holds. Its score is
the posterior probability that following it goes well, computed over the
outcomes of its uses — the same logit-space odds update ADR-0013 uses for
Facts, fed with outcomes instead of observations of truth.

The Rule set is the memory of the user's decisions about how Cortex should
behave: it starts empty, learns from corrections, shows provisional rules to
the user, promotes the ones that hold up, and retires the ones that stop
working.

## Concepts

Defined in the `CONTEXT.md` glossary (`## Rules`): **Rule**, **Rule
confidence**, **Staging**, **Split**, **Merge**, **Use attribution**,
**Feedback classifier**, **Rule declaration**. Use those terms; don't drift to
synonyms the glossary avoids.

A Rule is **not** a Fact. A Fact is scored on whether it is true (ADR-0013); a
Rule is scored on what happened when it was followed. They share the update
mathematics and nothing else.

## Data model

- `rules` (new): `id`, `condition`, `action`, `rationale`, `state`
  (`staging` | `definitive` | `archived`), `confidence`, `origin`
  (`dictated` | `correction` | `merge`), `superseded_by` (FK, nullable),
  `created_at`, `updated_at`, `archived_at`. Indexed on `(state)` and
  `(confidence)`.
- `rule_uses` (new): one row per application — `rule_id` (FK), `session_id`,
  `situation` (the request the rule was applied to), `outcome`
  (`approval` | `implicit` | `correction` | `scope_correction` | `neutral`),
  `occurred_at`. Indexed on `(rule_id, occurred_at)` and `(session_id)`.
  This table *is* the evidence store for rules: there is no separate
  `rule_evidence` table.

Both tables live in the same migration as the rest of the schema; the
`evidence` table of ADR-0013 does not exist in code (issue #83), so whoever
implements the rule system first writes the pure update function
(`cortex/learning/rules/scoring.py`) and the evidence engine reuses it.

## Update rule

```
logit(p') = logit(p) + Σ s(use) · ln(LR(outcome))
```

- `logit(p) = ln(p / (1−p))`; O(1) per use; deterministic.
- `s(use)` is the direction: `+1` for `approval` and `implicit`, `−1` for
  `correction`, `0` for `scope_correction` (a scope correction triggers a
  Split, it does not penalise the parent) and for `neutral`.
- `LR(outcome)` is the strength of the signal, from the table below.
- Confidence is recomputed as the posterior over **active** uses — those
  within the validity window. Uses that expire stop counting; they are not
  deleted.

### Calibration constants (isolated and documented)

| Constant | Value |
|---|---|
| prior (new rule) | 0.5 |
| `LR(approval)` | 20 (strong) |
| `LR(correction)` | 20 (strong, negative direction) |
| `LR(implicit)` | 3 (weak) |
| `LR(neutral)` / `LR(scope_correction)` | 1 (no movement) |
| validity window | 6 months |
| promote threshold | 0.9 |
| demote threshold | 0.7 |
| archive threshold | 0.3 |
| min positive uses (promotion) | 3 |
| min distinct sessions (promotion) | 2 |
| context budget (rules) | 40 |

These are the only magic numbers in the system, like ADR-0013's likelihood
ratios and windows. The rule "explicit approval is worth a lot, implicit
acceptance a little, silence nothing" is the whole calibration story: silence
must not count as approval or every rule would be promoted simply because
nobody complained.

## Lifecycle

| Event | Behaviour |
|---|---|
| Dictated by the user | born `definitive` (it is their decision, not a hypothesis), still subject to decay |
| Born from a correction | born `staging` |
| Applied in a turn | the classifier records a `rule_uses` row with the situation and the outcome category |
| Promotion | confidence ≥ 0.9 **and** ≥ 3 positive uses **and** ≥ 2 distinct sessions → `definitive`; Cortex notifies with one line |
| Demotion | confidence < 0.7 → back to `staging` (visible again) |
| Archive | confidence < 0.3, or on an explicit user command; the rule stays queryable but is never applied |
| Split | a scope correction replaces the rule with two complementary rules (see below) |
| Merge | the fused rule takes the union of the parents' uses; definitive only if all parents were definitive |
| Manual edit | a new dictated rule replaces the old one; the old is archived and **does not inherit** its history — the evidence was about a different text |

The hysteresis between 0.9 (promote) and 0.7 (demote) is deliberate: a
consolidated rule should not fall on the first bad day. Time alone never kills
a rule — expiry of its uses returns the confidence to the prior 0.5, which
demotes it to staging, where it becomes visible again. Only corrections
archive it.

## Split

A correction that shows a rule is too broad ("asking me before booking is
right for a dinner, not for a pizza") does not penalise the parent and does not
create an exception rule. It replaces the parent with two rules whose
conditions are disjoint, together covering the parent's situations.

- The child that preserves the parent's behaviour inherits the parent's state
  and its uses **filtered to the uses whose situation falls inside the child's
  condition** (judged once, at split time).
- The new child starts in `staging` and takes the parent's uses that fall
  inside its own condition.
- The parent is archived.

Filtering rather than copying is what keeps the two children's scores honest:
a pizza accepted in silence belongs to the pizza rule, not to the dinner rule.

## Use attribution and the feedback classifier

The reasoner's final output is free text or a tool call — there is no
structured field for rule IDs, and ADR-0005 rejected `response_format`/JSON
schema, ADR-0018 removed the text-marker convention. So attribution happens
**after** the turn, in the Learning module, asynchronously, with its own
`CircuitBreakerLLMClient` (there is no LLM priority queue in the codebase;
each module gets its own breaker).

Per turn, the classifier receives the active rules, the response, and the
user's next message, and returns per rule: *was it applied* and *what is the
outcome category*. It returns a category, never a number — the weight comes
from the table above, because a model's confidence in itself is not calibrated.
It does not read the model's thinking tokens.

Explicit user commands always win over the classifier: `R-14b no`, a 👍/👎 on
a rule, or the archive command.

If the classifier is down, the feedback queues and is not lost.

## Context

- Every active rule enters the context in v1.
- Beyond the context budget (40 rules) the retrieval of ADR-0008 takes over;
  rules with a universal condition always enter. ADR-0008 is designed but not
  implemented (no `LLMClient.embed()`, no vector column), so v1 does not need
  it.
- A staged rule additionally gets a one-line declaration in the response when
  it is applied, so the user can confirm or correct it. Definitive rules are
  silent — only the structured use attribution records them.

## Manual management

- `cortex rules list` / `cortex rules show <id>` — state, confidence, recent
  uses, history (splits, merges, replacements).
- Archiving is an explicit command and prevails over the classifier.
- There is no way to edit confidence or state directly: promotion and demotion
  are reached through feedback.

## Invariants

| Law | Negation → test |
|---|---|
| Active rules never contradict each other | two rules with overlapping conditions and incompatible actions cannot both be active |
| A rule's confidence equals the posterior over its active uses | recompute and compare |
| Confidence always in [0, 1] | out-of-range assertion |
| An expired use contributes nothing | posterior unchanged after the window passes |
| Silence never moves the score | a turn with no user reply leaves confidence unchanged |
| A rule only scores on uses it actually applied to | a rule present in context but not applied has no use row |
| A split's children have disjoint conditions and cover the parent's | condition check on both children |
| Archived rules are never applied | no rule in context has state `archived` |
| The Golden Set never enters the learning loop | during eval, no rule is created and no use is recorded |

## Out of scope for v1

- Environment signals (a tool failing because of the rule's action) — a rule
  must not decay for someone else's failure.
- Rules deduced by Cortex on its own — needs a periodic reflection over
  conversations that does not exist yet, and produces many spurious rules.
- Embedding-based conflict detection — with a budget of 40 rules the new rule
  is compared against the whole active set with an LLM.
- Embedding-based retrieval of rules in the context (ADR-0008).
- Reasoning-token feedback.

## Sources of truth

- `docs/adr/0020-learned-rules.md` — why (rejected alternatives, rationale).
- `CONTEXT.md` (`## Rules`) — vocabulary.
- This document — the system as a whole. **Keep it in sync with changes.**
