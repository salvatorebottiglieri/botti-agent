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
- Confidence is **recomputed** from the prior over **active** uses — those
  within the validity window — not accumulated in place. Recomputing is what
  makes expiry work: when a use falls out of the window it stops contributing
  and the posterior moves back toward the prior on its own, with no separate
  decay mechanism. Uses that expire are not deleted.
- The incremental form above is the per-use step; the stored confidence is
  always the recomputed posterior, so an expired use cannot leave a stale
  number behind (the same invariant ADR-0013's sweep enforces for facts).

### Calibration constants (isolated and documented)

| Constant | Value |
|---|---|
| prior (new rule from a correction) | 0.5 |
| prior (new rule dictated by the user) | 0.95 — the dictation is itself one `user_confirm`-strength evidence, so the rule starts definitive |
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

The two priors are not decoration. A rule born from a correction has no
evidence yet, so it starts at the prior 0.5 — and stays in staging, where the
demotion rule does not touch it. A rule dictated by the user starts with the
dictation as its first evidence (`logit(0.5) + ln(20) ≈ 0.95`), which is why it
is definitive from the start and why it does not immediately fall back to
staging: with the plain 0.5 prior it would sit below the 0.7 demotion
threshold and be demoted on the first sweep. Its dictation still expires with
the 6-month window, and an expired dictation brings it back to 0.5 — demoted
to staging, visible again.

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
a rule — expiry of its uses returns the confidence to the prior, which demotes
it to staging, where it becomes visible again. Only corrections archive it.

**Where a new rule comes from.** The classifier produces a *category per
existing rule*; it does not invent rules. A rule is created when the user
dictates one ("d'ora in poi chiedi conferma prima di prenotare") or when a
correction reveals one that no existing rule covers. Both are recognised by
the same classifier pass, which returns them as proposals rather than as
scores: a proposal carries the condition, the action and the rationale, and
goes through the conflict check below before being saved. A dictated rule is
born definitive; a proposed rule born from a correction is born in staging.

This is the one place where the classifier does more than classify: it also
proposes. Both outputs come from the same call, because both need the same
context — the active rules and the user's message — and splitting them would
mean two LLM calls to read the same message.

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
**after** the turn, asynchronously, with its own `CircuitBreakerLLMClient`
(there is no LLM priority queue in the codebase; `create_for_module()` exists
but is not called anywhere in `src/cortex/` — `main.py` builds one client with
`create()` — so the classifier wires that seam for the first time).

Per turn, the classifier receives the active rules, the assistant turn's
content, and the user's next message, and returns per rule: *was it applied*
and *what is the outcome category*. It returns a category, never a number —
the weight comes from the table above, because a model's confidence in itself
is not calibrated.

The classifier is a **new component** (issue #130), not an extension of an
existing one: the Learning Module is an Echo State Network over minion events
with no LLM client (ADR-0001) and is not mounted in the composition root. The
design fixes only its contract — asynchronous, own `CircuitBreakerLLMClient`,
never blocking the loop; where it lives is the implementation's call.

**The classifier reads the model's reasoning.** Reasoning reaches Cortex out
of band (`reasoning_content` / `reasoning` on the provider delta); the OpenAI
provider normalises it into a single inline `<think>...</think>` block
(ADR-0018), so it rides on the assistant turn's content and the classifier
receives the turn as the model produced it. The reasoning is where the model
explains *why* it acted — "the user wants confirmation before booking, so I
asked" — which is the strongest available signal that a Rule was applied,
stronger than inferring it from the response text. Where the provider surfaces
no reasoning, the classifier falls back to the text alone and attributes less;
it never invents an attribution.

Only the streaming path carries the reasoning today: `LLMClient.chat_stream()`
normalises the provider's reasoning field into the content deltas, while
`LLMClient.chat()` reads `message.content` and drops it. The implementation
must normalise the reasoning on the non-streaming path too (or give the
classifier the streamed content), otherwise the reasoning is available only on
the SSE path. On the streaming path the reasoning is already persisted: the
provider yields the `<think>` block as content deltas, so the assistant message
in `messages` contains it inline and the classifier can read the turn from the
session without a new column. The reasoning adds no new PII surface — it was
produced by the provider and already sits in that table; the classifier runs on
the same provider and the rule system stores no copy of it.

The loop's `ThinkingEvent` is **not** this reasoning: `Decision.reasoning` is
a placeholder ("Direct response to user"), so the `thinking` event carries no
model thought and must not be used as the classifier's input.

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
  it is applied, so the user can confirm or correct it. The **model** writes
  it, following a prompt instruction — it is written before the turn ends,
  while use attribution is decided after, so the two can disagree (the model
  announces a rule it did not really apply, or applies one silently). The
  classifier's attribution is the one that scores. Definitive rules are
  silent — only the use attribution records them.
- Rules reach the prompt through the memory bundle (`MemoryContext`), which
  today carries three dimensions — facts, personality, ambient — and is
  assembled by `ContextProvider` with per-dimension failure isolation
  (`degraded_dimensions`). Rules are a fourth dimension of that bundle, built
  with the same isolation: a failure to load the rules degrades the dimension
  and leaves the rest of the context intact.

## Evaluation

Learning is off during eval: no rule is created, no use is recorded. The
active rules are loaded as **fixtures**, so an eval measures behaviour *with*
rules and never modifies them. The Golden Set stays out of the learning loop
(`CONTEXT.md`).

The eval fixture schema (`EvalTask` in `src/cortex/eval/fixtures.py`) has no
field for rules today — it carries `turns`, `goal` and `sandbox` only. Loading
rules as fixtures therefore needs a new optional field on the task, with the
rule set supplied per task, and the runner must not enable learning for those
tasks.

## Manual management

- `cortex rules:list` / `cortex rules:show <id>` — state, confidence, recent
  uses, history (splits, merges, replacements). The colon form follows the
  existing CLI convention (`token:create`, `traces:cleanup`).
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
| A dictated rule starts definitive and does not immediately demote | a fresh dictated rule's confidence is ≥ 0.9 |
| A rule born from a correction starts in staging | a fresh correction-born rule's state is `staging` |
| The stored confidence equals the recomputed posterior | recompute from the active uses and compare |

## Out of scope for v1

- Environment signals (a tool failing because of the rule's action) — a rule
  must not decay for someone else's failure.
- Rules deduced by Cortex on its own — needs a periodic reflection over
  conversations that does not exist yet, and produces many spurious rules.
- Embedding-based conflict detection — with a budget of 40 rules the new rule
  is compared against the whole active set with an LLM.
- Embedding-based retrieval of rules in the context (ADR-0008).
- Reading the reasoning of a turn the classifier did not attribute (the
  classifier sees the reasoning of the turn it is judging, not of past turns).
- A dedicated UI for the rules — v1 exposes them through the CLI and the
  chat, like everything else.

## Sources of truth

- `docs/adr/0020-learned-rules.md` — why (rejected alternatives, rationale).
- `CONTEXT.md` (`## Rules`) — vocabulary.
- This document — the system as a whole. **Keep it in sync with changes.**
