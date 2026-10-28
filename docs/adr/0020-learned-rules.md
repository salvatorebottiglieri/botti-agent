# Learned rules: staged, scored by outcome

Cortex has no way to remember a decision the user made on its behalf. It stores
Facts (what is true about the user's world) and Goals, but a behavioural
instruction — "when I ask for a restaurant, ask me before booking" — is neither.
When the user corrects Cortex, the correction is answered and lost; the next
session repeats the same mistake.

We introduce the **Rule**: a condition → action → rationale prescription that
Cortex applies when the condition holds. A Rule is not a Fact. A Fact is
evaluated on whether it is true; a Rule is evaluated on what happened the times
it was followed. Mixing the two would corrupt the meaning of `Confidence` (the
posterior probability that a Fact is true) and the relevance ranking built on
it, so the Rule is a separate concept with its own lifecycle and its own score.

The score reuses the mathematics of ADR-0013 — an odds update in logit space,
a likelihood ratio per feedback source, and a validity window — but it is fed
by *outcomes of use* instead of observations of truth. A Rule starts at prior
0.5, moves up when the user approves it, down when the user corrects it, and
decays toward the prior as its evidence ages out.

Rules are not born definitive. A Rule born from a correction starts in
**staging**: it is present in the context like any other rule, it declares
itself to the user in one line whenever it is applied, and it is promoted only
after enough positive evidence accumulated across different sessions. A Rule
dictated directly by the user is born definitive — it is their decision, not a
hypothesis — but is still subject to decay.

The lifecycle is closed by two moves that keep the set small and consistent:

- **Split**. A correction that shows a Rule is too broad does not penalise it
  and does not create an exception rule. It *replaces* the Rule with two
  complementary Rules whose conditions are disjoint. The one that preserves the
  parent's behaviour inherits the parent's state and history, filtered to the
  uses that actually fall inside its condition; the new one starts in staging
  and takes the uses that fall inside its own.
- **Merge**. The inverse is proposed by Cortex — when the context budget is
  exceeded, or on a periodic pass over the Rule set — and always requires the
  user's approval, because generalising is exactly where an LLM invents.

The invariant that governs the whole set: **active Rules never contradict each
other**. Two Rules valid in the same situation must prescribe compatible
actions. It holds by construction under Split, and is checked at creation time
against the existing set.

Attribution — which Rules were actually applied in a turn — is decided *after*
the turn by an asynchronous feedback classifier, not by the reasoner. The
reasoner's final output is free text or a tool call; there is no structured
field to carry Rule IDs, and the text conventions ADR-0005 rejected and
ADR-0018 replaced are exactly what we do not want back. The classifier already
exists to categorise user feedback; it now also sees the active Rules, the
response and the model's reasoning, and returns, per Rule, whether it was
applied and what the user's next message says about it. One extra async call,
no extra loop iteration, no new convention on the wire.

## What "used" means

A Rule counts as used when the classifier recognises that it was applied in the
turn. Not "it was in the context" (a Rule about Jira must not score on a
dinner), and not "the model said so" (the model has no structured field in
which to say it). A use also records the *situation* — the request it was
applied to — which is what makes Split able to redistribute history instead of
copying it.

The classifier reads the turn's **reasoning** as well as its text, wherever the
model produced any. The reasoning is where the model states *why* it did what
it did ("the user always wants confirmation before booking, so I asked"), and
that is the closest thing to a declaration of use that exists without putting
a new field on the wire. Where the provider surfaces no reasoning, the
classifier falls back to the text alone and attributes less — a weaker signal,
never a fabricated one.

Feedback is categorised, never scored by the model's own confidence:

| Signal | Direction | Weight |
| --- | --- | --- |
| Explicit approval | + | strong (like `user_confirm`) |
| Correction of the decision | − | strong |
| Implicit acceptance (proceeding with the proposal) | + | weak |
| Silence / session end | none | 0 |

Silence is worth zero on purpose: if silence counted as approval, every Rule
would be promoted simply because nobody complained. Environment signals
(a tool failing because of the Rule's action) are out of scope for v1 —
attributing a tool failure to a specific Rule is easy to get wrong, and a Rule
should not decay for someone else's failure.

## Lifecycle

| Event | What happens |
| --- | --- |
| Dictated by the user | born definitive |
| Born from a correction | born in staging |
| Scope correction | Split into two complementary Rules; the one preserving the parent inherits state and compatible uses, the other starts in staging and takes its own uses |
| Conflict at creation | checked with an LLM against the existing set; for corrections Cortex proposes the Split directly, for a conflict with another definitive Rule the user decides; the replaced Rule is archived |
| Promotion | confidence ≥ 0.9 **and** ≥ 3 positive uses **and** ≥ 2 distinct sessions; Cortex notifies with one line |
| Demotion | confidence < 0.7 → back to staging, so it becomes visible again |
| Archive | confidence < 0.3, or on the user's explicit command |
| Merge | proposed by Cortex (budget exceeded, or periodic), approved by the user; the fused Rule takes the union of the evidence and is definitive only if all parents were |
| Manual edit | a new dictated Rule replacing the old one; the old is archived and its history is *not* inherited — the evidence was about a different text |

Evidence is valid for 6 months. Time alone never kills a Rule: it demotes it
back to staging, where it becomes visible again. Only corrections archive it.

## Context

In v1 every active Rule enters the context. Beyond a configurable budget (40)
the retrieval of ADR-0008 takes over, and Rules with a universal condition always
enter. The budget is also the signal that the set needs consolidation.

## Alternatives rejected

- **Rule as a new Fact type** — a Fact's score means "this is true"; a Rule's
  means "following this went well". Sharing the type would make `Confidence`
  mean two things and break the ranking of Facts.
- **A counter of positive uses** — "after N uses promote" ignores how confident
  the evidence was and cannot express decay.
- **Beta distribution over successes/failures** — a defensible alternative to
  the logit update, but it is a second calibration surface next to ADR-0013's,
  and the two would disagree on the same evidence.
- **A structured field in the reasoner's response** — `Decision` carries only
  text or tool calls; ADR-0005 rejected `response_format`/JSON schema to keep
  native function calling, and ADR-0018 removed the text-marker convention.
  Adding a field back means reviving the convention the project already left.
- **A `cite_rules(ids)` tool call** — structured and reliable, but it costs an
  extra loop iteration on every turn that uses Rules.
- **The model declaring the Rules it followed in the response text** — the same
  fragile marker convention as ADR-0005, and the model cannot be trusted to
  remember which of 40 Rules it applied.
- **Embedding-based conflict detection** — ADR-0008 is designed but not
  implemented (no `LLMClient.embed()`, no vector column). With a budget of 40
  Rules, v1 compares a new Rule against the whole active set.
- **Feedback from the environment (tool failures)** — out of scope in v1; a
  failure caused by the environment must not decay the Rule.
- **Rules deduced by Cortex on its own** — needs a periodic reflection over
  conversations that does not exist yet, and produces many spurious Rules. It
  comes after (a) and (b) have produced data on how well the score works.

## Implementation notes

- **The EvidenceStore of ADR-0013 does not exist in code** (issue #83): there is
  no `evidence` table and no `EvidenceStore`. The Rule score reuses ADR-0013's
  *mathematics*, not a component. Whichever of the two systems is implemented
  first writes the pure update function; the other reuses it.
- **There is no LLM priority queue.** Each module gets its own
  `CircuitBreakerLLMClient` through `LLMClientFactory.create_for_module`, and
  the classifier is asynchronous with its own breaker. If the classifier is
  down, the feedback queues and is not lost.
- **The classifier reads the model's reasoning where the provider surfaces it.**
  Reasoning arrives out of band (`reasoning_content` / `reasoning`) and the
  OpenAI provider normalises it into a single inline `<think>...</think>` block
  (ADR-0018), so it rides on the assistant turn's content. The classifier is
  given the turn as the model produced it; where no reasoning was produced,
  attribution falls back to the text alone.
- **Only the streaming path carries the reasoning today.**
  `LLMClient.chat_stream()` normalises the provider's reasoning field into the
  content deltas; `LLMClient.chat()` (the non-streaming path) reads
  `message.content` and drops it. The implementation must normalise the
  reasoning there too — or the classifier must read the turn's streamed
  content — otherwise the reasoning is available only on the SSE path.
- **The reasoning adds no new PII surface.** It was produced by the provider
  and already sits in the `messages` table; the classifier runs on the same
  provider as the reasoner and reads the turn from that table. No new copy of
  the reasoning is stored by the rule system.
- **The `ThinkingEvent` is not the reasoning.** `Decision.reasoning` is a
  short placeholder ("Direct response to user"), so the loop's `thinking`
  event carries no model thought and must not be used as the classifier's
  input. The reasoning is the inline block in the content.
- **The Golden Set never enters the learning loop** (`CONTEXT.md`): during
  evaluations Rule learning is off, no Rule is created and no evidence is
  recorded. Active Rules are loaded as fixtures, so an eval measures behaviour
  with Rules and does not modify them.
- **Thresholds are calibration constants**, isolated and documented like
  ADR-0013's likelihood ratios and windows. The mechanics live in
  `docs/specs/rule-system.md`.
