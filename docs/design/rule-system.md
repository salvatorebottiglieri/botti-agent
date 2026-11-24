# Program Design: rule-system — ticket #168: a turn becomes a Feedback

Spec: `docs/specs/rule-system.md` (slice B). Parent: #166. Frontier ticket: #168.

**Status: approved** — the one load-bearing choice below is signed by the owner in chat, 2026-11-20. The "categories, never numbers" rule is not a new choice: it is ADR-0020's, restated in the spec.

_CLASSIFY: new capability inside an existing module (one new seam, no schema in this ticket, no external contract)_

## Shape

- **`cortex.rules.feedback`** (new): `FeedbackClassifier` — one `chat()` per turn, returns a `Feedback`. It hides the prompt, the parsing and the tolerance rules; the caller sees only `Feedback`.
- **`cortex.rules.attribution`** (new): `AttributionRun` — takes a `FeedbackClassifier` and a `RuleService`, writes the uses through `RuleService.record_use`, and never lets a classifier or storage failure escape: a failed turn writes nothing and the caller is told nothing happened.
- **`cortex.llm.providers.openai.OpenAIClient.chat`** (modified): normalises out-of-band reasoning into the inline `<think>` block that `chat_stream()` already produces, using the same `_reasoning_delta` helper, so a non-streaming turn carries the reasoning the classifier reads.
- **Not changed:** `RuleService`, the repository, the inference of a Rule's state, the SSE adapter, the CLI. The queue, the proposals path and the wiring into the chat endpoint belong to #169, #170 and #172 — this ticket runs the classifier and the run directly, against the in-memory repository.

## Decision 1 — the classifier runs only on a turn that can be attributed

- **Choice**: no call when the turn had no Rules in its context, and no call when the user's reply is blank. Silence is not evidence, and a turn with no Rule in context has nothing to attribute.
- **Alternatives rejected**: one call per turn regardless (every turn of every session pays a provider call for nothing); trigger on the text looking like feedback (a heuristic that misses derivations and is invisible to the user when it is wrong).
- **Consequences**: the number of calls is bounded by turns that had Rules and got an answer, which is what a Rule system actually needs.
- **Owner sign-off**: 2026-11-20, owner in chat — "Ok tutte" (to the four recommendations of the phase-1 interview, of which this is the first).

## Ticket #169 — the feedback queue

_Written 2026-11-20, before the code. Signatures and contracts are in the
`json design` block below, under their own entries._

The queue is what makes attribution survive a provider outage or a restart: a
turn that had Rules in context and got a reply is written as a pending row, and
a later pass attributes it. Three shapes: the row (`QueuedFeedback`), the port
(`FeedbackQueue`), the Postgres implementation, plus `FeedbackDrain` that ties
the queue to #168's `AttributionRun`.

**No claim state.** Two drains may pick the same pending row and both classify
it; the partial unique index on `rule_uses (feedback_id, rule_id)` means the
second writes nothing, so the guarantee that matters — one turn never scores a
Rule twice — is held by the database, not by a locking protocol. A second drain
can therefore redo a classification that is still in flight: wasted work, never a
wrong score.

## Decision 2 — the queue needs no claim state

- **Choice**: three states (`pending`, `attributed`, `dropped`) and no
  `FOR UPDATE SKIP LOCKED` claim; idempotency comes from the partial unique
  index on `rule_uses (feedback_id, rule_id)`.
- **Rejected**: a fourth `claimed` state with a locking claim (it holds a
  connection across an LLM call, or needs a lease and a timeout); keeping the
  ticket's original "two drains cannot both claim the same row" criterion (it
  protects against a harm the index already prevents).
- **Consequences**: `RuleUse` gains a nullable `feedback_id`, the drain becomes a
  plain loop, and the ticket's criterion was rewritten to "no double scoring".
  The cost is a duplicated provider call when two drains race.
- **Owner sign-off**: 2026-11-20, owner in chat — "A" (to the two options, A =
  drop the claim criterion and rely on the unique index, B = add the `claimed`
  state).

## Ticket #170 — proposals and the conflict check

_Design, 2026-11-20, before the code. Signatures and contracts are in the
`json design` block._

The classifier already returns at most one proposal per turn (#168) and the run
ignores it. #170 is the short path from that proposal to a stored Rule, and the
one check that stands between them.

- **`cortex.rules.conflicts.ConflictCheck`** — one `chat()` call comparing the
  candidate with the active set. `True` when the candidate contradicts an active
  Rule. An empty active set returns `False` without a provider call: there is
  nothing to contradict, and an ordinary first rule must not cost a second call.
- **`AttributionRun` gains an optional third collaborator.** With a
  `ConflictCheck` the run applies the proposal after writing the uses: a
  dictated proposal goes through `create_dictated` (definitive, the dictation as
  its first use), a correction-born one through `create_from_correction`
  (staging). Without one the run behaves exactly as in #168, which is what keeps
  the existing tests and the eval-free path unchanged.
- **The budget is the service's.** `list_active()` already returns at most
  `CONTEXT_BUDGET` rules, so the run does not slice again; the check sees exactly
  what the context would see.
- **One handler covers both.** A conflict, a provider error or malformed output
  drops the proposal and never the uses: the turn's evidence is already written
  by then, and a proposal is the cheaper thing to lose.
- **Return value.** The run returns every Rule it touched — the ones whose use
  it recorded and the one it created — so a caller can refresh or announce them
  without a second read.

## Ticket #171 — the split path

_Design, 2026-11-20, before the code. Signatures and contracts are in the
`json design` block._

The service can already split a rule (`RuleService.split`, slice A: two
children, `parent_id` on both, `superseded_by` = the first child, the parent
archived, every use reassigned, everything validated before the first write).
#171 is the other half: the reading of the correction that produces the cut.
The classifier says *that* the rule was too broad (`scope_correction`); a
second call says *how* to cut it.

- **`cortex.rules.splits`** (new): `SplitProposer` — one `chat()` per scope
  correction. The prompt carries the rule and its uses as `<n> | <situation> →
  <outcome>`, and asks for the two children plus, for every index, the child
  that inherits that use. The assignment is **positional**: no use id and no
  rule id enters the prompt, and the run maps the index back to the use it
  already holds.
- **`AttributionRun` gains a fourth optional collaborator**, `splits`,
  symmetric with `conflicts`: after the uses are written, each
  `scope_correction` entry asks the proposer and hands its answer to
  `RuleService.split`. Without a proposer the run behaves exactly as in #168 —
  the use is recorded, nothing splits — which is what leaves the existing tests
  and the eval path unchanged.
- **`RuleService.list_uses`** (one-line pass-through on the published
  interface): the run needs the parent's uses — the situations the proposer
  reads — and the repository stays private to the module.
- **Order matters.** The use is written first, so the split's own `list_uses`
  already contains it and it is redistributed with the others; a
  `scope_correction` use is worth `ln(1) = 0`, so the parent's score does not
  move before it is archived.
- **The prompt orders the children.** `RuleService.split` gives the parent's
  state to child 0, so the proposer is told that the first child is the one that
  keeps the current behaviour and the second is the new, narrower rule; a model
  free to order them would hand a definitive rule's confidence to the wrong
  text.
- **A split with no uses leaves both children in `staging`** (ticket AC 4):
  with no evidence there is no child that preserves the parent's behaviour, so
  neither may inherit its state. With at least one use, child 0 still inherits
  what slice A pinned.
- **One handler, as in #170.** A failed proposer, a malformed answer, an
  assignment that does not cover every use: the uses stand, the parent stays
  active, nothing else is written. A split is lost, never half-made — the
  service validates before its first write, which is why the run may hand it an
  unvalidated assignment.

### Exits for #171

`tests/unit/rules/test_splits.py` — the proposer and its parse: two children
and a positional assignment parsed; every use listed with its index, situation
and outcome; one or three children refused; a blank condition or action
refused; an assignment that is not a list of `0`/`1` of the right length
refused; fenced or `<think>`-prefixed JSON accepted; a malformed reply raises
`ValueError`.

`tests/unit/rules/test_attribution.py` — the path through the run: a scope
correction splits the parent and redistributes the parent's uses to the two
children; without a proposer the use is recorded and nothing splits; a plain
correction never asks the proposer; a failing proposer leaves the parent
active with its use written; the returned list carries the children.

`tests/unit/rules/test_service.py` — a split with no uses leaves both children
in `staging`.

## Decision 3 — the split is a second call, not part of the per-turn classification

- **Choice**: the per-turn classifier keeps its current answer (uses plus at
  most one proposal), and the two children plus the use assignment come from a
  second call — `SplitProposer`, made only when a `scope_correction` is
  attributed.
- **Alternatives rejected**: (A) the classifier returns the split too, in the
  same call — it would have to carry the uses of every rule in context in every
  turn's prompt, so every attributed turn pays for a case that is rare.
- **Consequences**: one extra provider call per scope correction (a rule is
  split rarely, and only then); the classifier's prompt and its tests are
  untouched; the two LLM seams (`ConflictCheck`, `SplitProposer`) have the same
  shape — optional, `None`-safe, called only when their trigger fires; a
  scope correction whose proposer is missing or failing records the use and
  leaves the rule as it is, which costs one wrong application, never a score.
- **Owner sign-off**: 2026-11-20, owner in chat — "B" (to the two options: A =
  the classifier returns the split in its own call, B = a second call only on a
  scope correction).

## Ticket #172 — the real chat turn is attributed

_Design, 2026-11-23, before the code. Signatures and contracts are in the
`json design` block._

Everything up to #171 is a library nobody calls: `main.py` builds a
`RuleService` and no `AttributionRun`, so no turn is ever enqueued and no rule
is ever scored in the running system. #172 is the wiring, and nothing else —
no new component, no new table, no change to the classifier, the queue, the run
or `AgentLoop`.

- **`cortex.rules.queue.enqueue_turn`** (new): one function the loop's callers
  use — `queue`, the turn's `rules`, `session_id`, `assistant_turn`,
  `user_reply` in, the row or `None` out. It writes nothing when the turn had
  no rules or the reply is blank, and it swallows a storage failure with a log
  line: the turn already happened, so a lost row costs one turn of evidence and
  never a reply. The blank-reply `CHECK` and the guard in `FeedbackQueue.enqueue`
  stay the last line of defence, not the only one.
- **`cortex/api/routes/chat.py`** (modified): both endpoints read the turn's
  rules before the turn and call `enqueue_turn` after the answer — the
  non-streaming one after `run_chat` returns, the streaming one on the
  `ResponseDoneEvent` it already matches (which a clarifying question also
  emits), so a cancelled stream simply enqueues nothing. One `INSERT` is the
  only added latency, and it never raises into the response.
- **`cortex/api/dependencies.py`** (modified): `get_rule_service()` and
  `get_feedback_queue()` beside the existing getters; both keys are added to the
  state dict in `main.py`.
- **`src/cortex/main.py`** (modified): the composition root builds
  `FeedbackClassifier(llm_factory.create_for_module("rules"))`, `ConflictCheck`
  and `SplitProposer` on that same client, `AttributionRun(..., conflicts, splits)`,
  `PostgresFeedbackQueue()` and `FeedbackDrain(...)`, and starts
  `_feedback_drain_loop` beside `_evidence_sweep_loop`. The drain is the only
  place attribution runs: it never runs inside a request, so a slow or failing
  classifier cannot reach a user.
- **`FEEDBACK_DRAIN_INTERVAL_SECONDS = 60`** in `main.py`, next to
  `EVIDENCE_SWEEP_INTERVAL_SECONDS`: the spec's "a correction is learned a
  moment later" is a minute, not the evidence sweep's hour.
- **Not changed, deliberately:** `AgentLoop` (P6), the eval runner (it calls
  `AgentLoop.stream_chat` directly, so eval never enqueues — the "eval never
  learns" invariant is untouched, which is #175's other half), and the CLI: it
  has no chat command today (`src/cortex/cli.py` holds `rules:*`, `memory:*`,
  `backup:*`, `undo`, `version`), so the spec's "the CLI enqueues" has no seat
  until a CLI turn exists.

### Exits for #172

`tests/unit/rules/test_queue.py` — the helper: one row with the turn's rule ids
when the turn had rules and a reply; nothing without rules; nothing for a blank
reply; nothing and no exception when the store fails.

`tests/unit/api/test_chat_stream.py` — the route: one row after the answer,
carrying the rules read before the turn, the assistant text and the user
message; nothing when the snapshot was empty; one row on a clarifying question;
nothing on an error frame; the response unchanged when the enqueue fails.

`tests/unit/api/test_chat_attribution.py` (new) — the same for the
non-streaming endpoint.

`tests/unit/test_main_wiring.py` (new) — the composition root builds the run
and the drain and starts the drain task (the existing `main` wiring tests are
the pattern); the drain loop survives a raising drain.

`tests/e2e/test_rule_attribution.py` (new) — end to end with a scripted
provider: a chat turn with a staged rule in context, then the drain, moves that
rule's confidence and state, and `RuleService.list_active()` — what
`cortex rules:list` prints — reflects it.

## Decision 4 — the enqueue reads the active set at the turn's start

- **Choice**: the caller snapshots `RuleService.list_active()` before the turn
  and writes that snapshot as the row's `rule_ids` after the answer. That call
  is the bundle's own source (`context_provider.py:94` → `list_active()` at
  `:109`), so the row names the rules the model was about to see.
- **Alternatives rejected**: (B) `ResponseDoneEvent` carries the turn's rules —
  exact, but it changes `AgentLoop`, which the spec pins as unchanged (P6, "The
  seam is the loop's caller"); (C) a callback parameter on `run_chat` /
  `stream_chat` — the same change with a wider surface, since every caller
  (endpoints, eval runner, tests) would have to supply it.
- **Consequences**: a rule created or archived by a concurrent drain in the
  milliseconds of a single turn can be missing from the row (under-attribution)
  or present although the bundle was built without it (over-attribution). Both
  are one turn wide, and the first is the direction the spec's Degradation
  decision already prefers; the invariant "a use names a rule that was in that
  turn's bundle" is still held by the classifier, which drops an id it was not
  given.
- **Owner sign-off**: 2026-11-23, owner in chat — "A" (to the two options: A =
  read `list_active()` at the caller, before the turn; B = carry the rules on
  the `ResponseDoneEvent`).

## Ticket #173 — a promotion announces itself

_Design, 2026-11-23, before the code. Signatures and contracts are in the
`json design` block._

The sweep promotes silently: `RuleService` recomputes the posterior, `next_state`
returns `definitive`, the row is refreshed, and nobody hears about it (the spec
files this as "Not in slice A"). #173 is the one line the user reads.

- **`cortex.events.types.EventTypes.RULE_PROMOTED`** (`rule.promoted`): the
  name the spec's module map already draws as the only new edge
  (`rules -. rule.promoted .-> agentic`).
- **`cortex.rules.render.promotion_line(rule)`** (new, pure, beside `render_rules`
  which owns the same shape): the sentence,
  `Rule confirmed: <condition> → <action>` — the same `condition → action` shape
  `render_rules` gives the model, fixed copy and no provider call, so the
  announcement cannot hallucinate and costs nothing.
- **`RuleService(repository, *, clock=..., emitter=None)`**: after a refresh
  that took the state from `staging` to `definitive`, emit `rule.promoted` with
  the refreshed rule's id, condition, action and the line. Both writers that can
  promote go through one helper — `record_use` and the sweep inside
  `list_active`. A demotion or an archive emits nothing (AC 4), and no emitter
  means no event, so the CLI and every existing test keep working.
- **`cortex.agentic.notices.PromotionNotices`** (new, no options): the agentic
  side of the edge. `handle(event)` appends the line carried by a `rule.promoted`
  event and ignores anything else; `take()` pops one line, FIFO. Bounded at 20
  lines, hardcoded, the oldest dropped: a line nobody read for weeks is noise,
  and dropping it is the same loss a failed delivery already accepts.
- **`AgentLoop(notices=None)`**: at the start of a chat turn it takes at most
  one line and yields it as the turn's **first** `TextDeltaEvent`, `line + "\n"`
  (the sentence on its own line, never glued to the answer). One prefix, one
  place: `run_chat` accumulates the same delta, the streamed path already
  emitted it before the answer, and **every** terminal `done` message of the
  turn carries it — RESPOND, a clarifying question, the no-tools fallback — so
  the three views of one turn always agree. The prefix **never enters the
  session history**: `add_message` stores the model's own text, because the
  line is not the model's words and history is what the model reads next turn.
  No notices → the loop emits exactly what it did before.
- **`main.py`**: `PromotionNotices()` built with the loop, subscribed to
  `EventTypes.RULE_PROMOTED` after the bus starts, `RuleService` given
  `EventEmitter(cortex.event_bus, "rules")`. The event is published after the
  refresh returns — after commit — and `EventEmitter.emit` already turns a bus
  failure into a log line, so a lost line never costs the promotion (AC 3).
- **Not changed, deliberately**: the goal path (`run_goal` assembles its own
  text and has no user turn to prefix); the eval runner (it calls the loop but
  must never learn — #175); the CLI (no chat turn). The promotion is still
  silent for those callers, which is what "the next response that has one"
  means for a chat product.

### Exits for #173

`tests/unit/rules/test_render.py` (new) — the line: the condition and the
action in the `→` shape; no rationale, no id, no confidence in the sentence.

`tests/unit/rules/test_service.py` — the emission: a `record_use` crossing the
threshold emits exactly one `rule.promoted`, after the refresh, carrying the
refreshed rule and the line; the sweep in `list_active` emits one for the rule
it promotes; a demotion emits none; an archive emits none; a service built
without an emitter emits nothing and still promotes; a bus that raises leaves
the promotion stored.

`tests/unit/agentic/test_notices.py` (new) — the buffer: a `rule.promoted` event
enqueues its line; `take()` returns the lines FIFO and `None` when empty; the
cap drops the oldest; an event of another type or without a line is ignored.

`tests/unit/agentic/test_loop.py` — the turn: a pending line is the first
`TextDeltaEvent` and the `done` message starts with it; the turn after it has
none; with no notices the stream is byte-identical to today.

`tests/unit/test_main_wiring.py` — the root: the service gets an emitter and the
loop gets the notices; the handler is subscribed to `rule.promoted`.

## Decision 5 — one line per response, FIFO

- **Choice**: the loop takes **one** pending line per chat turn, oldest first.
  `Rule confirmed: <condition> → <action>` is fixed copy, prepended to the
  answer.
- **Alternatives rejected**: (B) take every pending line into the next
  response — a burst of promotions turns one answer into a wall of system
  lines, and a turn that fails loses all of them at once.
- **Consequences**: a second promotion is announced in the response after its
  own; a turn that ends in an error, or a stream the client cancels before the
  first frame, loses its line — the cost the AC already accepts. The wording is
  not the model's, so the line is identical in every language, costs no provider
  call and is kept out of the session history (the model must not learn to say
  it). Two sweeps racing on the same rule can both see `staging` and announce
  the same promotion twice; the line is content-neutral and the sweep is already
  non-transactional (a later ticket, if it ever matters).
- **Owner sign-off**: 2026-11-23, owner in chat — "A" (to the two options: A =
  one line per response, FIFO; B = every pending line in the next response),
  with the proposed wording accepted.

## Ticket #174 — a new dictation replaces the old Rule

_Design, 2026-11-23, before the code. Signatures and contracts are in the
`json design` block._

The spec's Manual edit row has been contract, not code, since slice A: dictating a
rule that supersedes one already known creates a second rule beside the first, and
`rules:show` has no edge to show. #174 is the recognition and the archive.

- **The classifier names the rule it replaces.** `RuleProposal` gains
  `replaces: UUID | None`. The prompt already lists every rule in context as
  `<rule_id> | <condition> → <action>`, so the field costs no extra call and no
  extra listing: it is the same evidence answering one more question. The parse
  drops a `replaces` that is not one of the listed ids, exactly as it drops an
  invented `rule_id` in a use — a proposal may only replace a rule the model could
  see.
- **`RuleService.create_dictated(session_id, spec, *, replaces: UUID | None = None)`**:
  with no `replaces`, nothing changes. With one: the predecessor must exist and must
  not already be archived (validated **before** the first write, as `split` and
  `merge` do), then the new rule is created from the dictation alone (definitive, its
  own `approval` use, no use of the predecessor), then the predecessor is archived
  with `superseded_by = new.id` and refreshed. The predecessor **keeps its uses**: the
  evidence was about a different text, and carrying it over would score the new
  wording on the old record.
- **`AttributionRun._apply_proposal`** passes `proposal.replaces` through for a
  dictated proposal only; a correction proposal ignores it (a correction creates a
  rule that was missing, it does not rewrite one).
- **`rules:show`** needs no change: the predecessor prints `superseded by: <new id>`,
  and from the new rule's side the existing `replaced:` section reads
  `list_children`, which covers `superseded_by` as well as `parent_id`. The edge is
  therefore visible in both directions with the code that exists.
- **Degradation**: a `replaces` naming an unknown id is dropped in the parse, and one
  naming an archived rule is refused by the service with `RuleReplacementRefusedError`
  — a `ValueError` subclass, so a caller that already treats a refusal as a `ValueError`
  keeps working — and the run retries once without `replaces`: losing the replacement
  costs a duplicate, never the user's rule. A predecessor that vanishes between the
  check and the archive is logged and costs the edge, not the successor.
- **The returned list is the post-replacement state.** `_apply_proposal` returns every
  rule the proposal moved (the successor and, for a replacement, the archived
  predecessor) and `_reflect` swaps them into the list `attribute` returns, the same
  way the split path already patched its archived parent.
- **Accepted race (shortcut)**: two replacements racing on one predecessor both pass an
  archived check that reads before it writes, so the second overwrites `superseded_by`
  and two definitive successors keep one edge. It needs a state-predicated `UPDATE`
  that the repository does not have; the code carries the `shortcut:` comment.

### Exits for #174

`tests/unit/rules/test_feedback.py` — the parse: a proposal carrying `replaces` keeps
it; an unknown id is dropped to `None`; a malformed id is dropped; a proposal without
the field is `None` (today's behaviour).

`tests/unit/rules/test_service.py` — the replacement: a dictation with `replaces`
creates the successor with no use of the predecessor, archives the predecessor with
`superseded_by` pointing at the successor, and leaves the predecessor's uses
untouched; a dictation with no `replaces` creates a second active rule exactly as
before; an unknown predecessor raises before anything is written; an already archived
predecessor raises before anything is written; the successor's confidence is its own
dictation (sigmoid(ln 20) ≈ 0.952), not the predecessor's posterior.

`tests/unit/rules/test_attribution.py` — the run: a dictated proposal with `replaces`
reaches the service with it; a correction proposal ignores it.

`tests/unit/rules/test_cli_rules.py` — `rules:show`: the predecessor lists
`superseded by:` and the successor lists the predecessor under `replaced:`.

## Decision 6 — the classifier names the rule a dictation replaces

- **Choice**: a dictated proposal carries `replaces: <rule_id>`, validated against
  the ids the classifier was given; the service archives the predecessor and links
  the edge.
- **Alternatives rejected**: (B) matching the dictation's `condition` text against
  the active set — no LLM change, but a reworded dictation creates a duplicate
  instead of a replacement, and an accidentally identical phrasing archives a rule
  the user did not mean to touch.
- **Consequences**: one more field in the proposal JSON and one prompt line; a
  reworded dictation still replaces (the classifier judges intent); the archive is
  what the spec's Manual edit row always asked for, and the uses stay with the text
  they were about.
- **Owner sign-off**: 2026-11-23, owner in chat — "A" (to the two options: A = the
  classifier names the rule it replaces; B = the service matches the condition text).

## Premises (design)

Inherited: P1-P10 of the spec. New:

| # | Premise | Command | Output | Verdict |
|---|---|---|---|---|
| P11 | `chat()` and `chat_stream()` share a helper for out-of-band reasoning, so normalising `chat()` is one call, not a second implementation | `rg -n "_reasoning_delta" src/cortex/llm/providers/openai.py` | `openai.py:30: def _reasoning_delta(delta)`, called at `:247` in the stream loop and nowhere else | confirmed |
| P12 | `GenerationConfig` already carries the fields a deterministic classification needs, so no settings change is required | `rg -n "class GenerationConfig" -A20 src/cortex/llm/config.py` | `temperature`, `max_tokens`, `top_p`, `seed`, `response_format` and more, all optional | confirmed |
| P13 | The rules tests already use an in-memory `RuleRepository` with the Postgres semantics (ids and ordering), so this ticket needs no database | `tests/unit/rules/fakes.py` | `InMemoryRuleRepository` — copies on read/write, ids assigned, ordering by confidence | confirmed |
| P14 | Nothing in the composition root constructs the attribution path, so no turn is enqueued at runtime | `rg -n "AttributionRun\|FeedbackDrain\|PostgresFeedbackQueue" src/cortex/main.py` | no match |
| P15 | No route enqueues a turn yet | `rg -n "enqueue" src/cortex/api src/cortex/execution src/cortex/main.py` | no match |
| P16 | The composition root has exactly one periodic task to sit beside | `rg -n "create_task" src/cortex/main.py` | `main.py:364` — the evidence sweep |
| P17 | The CLI has no chat turn to attribute, so the spec's "the CLI enqueues" has no seat today | `rg -cn "chat" src/cortex/cli.py` | `0` matches |
| P19 | The promotion is decided in exactly two places, both inside `RuleService` | `rg -n "next_state" src/cortex/rules/service.py` | `:120` (record_use) and `:149` (the sweep in list_active) | confirmed |
| P20 | The house publish seam already swallows a bus failure, which is AC 3 | `sed -n '20,45p' src/cortex/events/emitter.py` | `emit` logs a warning and returns; it never raises | confirmed |
| P21 | The event names are a closed registry | `rg -n "class EventTypes" -A6 src/cortex/events/types.py` | a `StrEnum`; `validation` already emits its own through `EventTypes` | confirmed |
| P22 | The loop's text reaches both callers through the delta stream, so one prefix delta is enough | `sed -n '113,132p;262,270p' src/cortex/agentic/loop.py` | `run_chat` accumulates `TextDeltaEvent.delta`; the RESPOND branch emits the whole text as one delta when it did not stream | confirmed |
| P23 | The classifier's prompt already lists every rule as `<rule_id> | <condition> → <action>`, so a `replaces` field needs no extra call or listing | `sed -n '19,45p' src/cortex/rules/feedback.py` | the system prompt's first lines and the JSON shape, which names `origin` and would name `replaces` | confirmed |
| P24 | `rules:show` already prints both directions of the history edge | `sed -n '225,240p' src/cortex/cli.py` and `src/cortex/rules/service.py:170-177` | `list_children` is `superseded_by = $1 OR parent_id = $1`; the CLI prints `superseded by:` and the `replaced:` section | confirmed |
| P25 | `create_dictated` has exactly one caller, so the new argument has one site to reach | `rg -n "create_dictated" src/` | `service.py:49` (definition) and `attribution.py:124` (the proposal path); the CLI never calls it | confirmed |
| P18 | The per-module LLM client the classifier wants exists | `rg -n "create_for_module" src/cortex/llm/factory.py` | `:56` — the definition (no caller under `src/` yet, P2 of the spec) |

## External fixtures

None. The provider is faked in every test (`MagicMock(spec=LLMClient)` as `tests/memory/test_conversation_extractor.py` does, or the scripted client of `tests/eval/fakes.py`); no response of a real provider is captured, and none is needed: the classifier's contract is with its own prompt, and the provider's shapes are pinned by `tests/unit/test_llm.py`.

## Exits (test list for the implementer)

In `tests/unit/rules/test_feedback.py`, `tests/unit/rules/test_attribution.py` and `tests/unit/test_llm.py`; no network, no database.

- `test_chat_normalises_reasoning_into_an_inline_think_block`: a non-streaming response whose delta carries reasoning produces `...<think>...</think>` in the content, exactly as `chat_stream()` does.
- `test_chat_without_reasoning_is_unchanged`: no `<think>` block, content untouched.
- `test_blank_reply_makes_no_call`: empty and whitespace replies → no LLM call, `Feedback` with no uses and no proposals.
- `test_no_rules_makes_no_call`.
- `test_a_reasoning_turn_is_what_the_classifier_sends`: the request body contains the assistant turn verbatim, including its `<think>` block.
- `test_one_use_per_rule_and_a_second_entry_for_the_same_rule_is_dropped`.
- `test_an_invented_rule_id_is_dropped`.
- `test_an_unknown_category_is_dropped`.
- `test_a_rule_in_context_but_not_applied_gets_no_entry`.
- `test_malformed_json_raises_value_error`.
- `test_fenced_json_is_accepted`.
- `test_a_proposal_with_blank_condition_or_action_is_dropped`.
- `test_a_proposal_with_an_unknown_origin_defaults_to_correction`.
- `test_the_run_writes_one_use_per_attributed_rule_through_the_service`: confidence and state move for the staged Rule.
- `test_the_run_writes_nothing_when_the_classifier_raises`: no use row, no error to the caller.
- `test_the_run_skips_a_rule_that_vanished`: `RuleNotFoundError` from the service is logged and the other uses are still written.
- `test_the_run_writes_nothing_when_the_repository_fails`: the storage error is logged, not raised.

## JSON design

```json design
{
 "feature": "rule-system",
 "call_stack": {
  "name": "entrypoints",
  "children": [
   {
    "name": "cortex.rules.attribution.AttributionRun.attribute",
    "note": "#168 the turn's rules, the assistant turn and the reply in; the refreshed rules out",
    "children": [
     {
      "name": "cortex.rules.feedback.FeedbackClassifier.classify",
      "note": "#168 one provider call, tolerant parse",
      "children": [
       {
        "name": "cortex.rules.feedback._parse_feedback",
        "note": "#168 pure: JSON to Feedback, drops what it cannot trust",
        "children": []
       }
      ]
     },
     {
      "name": "cortex.rules.service.RuleService.record_use",
      "note": "unchanged; recomputes and refreshes per use",
      "children": []
     }
    ]
   },
   {
    "name": "cortex.llm.providers.openai.OpenAIClient.chat",
    "note": "#168 normalises out-of-band reasoning into the inline think block",
    "children": [
     {
      "name": "cortex.llm.providers.openai._reasoning_delta",
      "note": "#168 the helper the streaming path already uses",
      "children": []
     }
    ]
   },
   {
    "name": "cortex.rules.queue.FeedbackDrain.drain",
    "note": "#169 one pass over the pending rows; returns how many were attributed",
    "children": [
     {
      "name": "cortex.rules.queue.FeedbackQueue.list_pending",
      "note": "#169 oldest pending first",
      "children": []
     },
     {
      "name": "cortex.rules.service.RuleService.get",
      "note": "#169 the rules of that turn, by id",
      "children": []
     },
     {
      "name": "cortex.rules.attribution.AttributionRun.attribute",
      "note": "#168 unchanged",
      "children": []
     },
     {
      "name": "cortex.rules.queue.FeedbackQueue.mark_attributed",
      "note": "#169 only when attribute returned a list, never on None",
      "children": []
     },
     {
      "name": "cortex.rules.queue.FeedbackQueue.drop_stale",
      "note": "#169 rows past the validity window",
      "children": []
     }
    ]
   },
   {
    "name": "cortex.rules.conflicts.ConflictCheck.contradicts",
    "note": "#170 one provider call, only when the turn proposed something",
    "children": []
   },
   {
    "name": "cortex.api.routes.chat.chat",
    "note": "#172 reads the turn's rules before the turn, enqueues the row after run_chat returns",
    "children": [
     {
      "name": "cortex.rules.service.RuleService.list_active",
      "note": "#172 the snapshot; the bundle's own source",
      "children": []
     },
     {
      "name": "cortex.rules.queue.enqueue_turn",
      "note": "#172 one row, or none when there is nothing to attribute",
      "children": [
       {
        "name": "cortex.rules.queue.FeedbackQueue.enqueue",
        "note": "#169 unchanged",
        "children": []
       }
      ]
     }
    ]
   },
   {
    "name": "cortex.rules.service.RuleService.record_use",
    "note": "#173 emits rule.promoted after the refresh that took the rule to definitive",
    "children": [
     {
      "name": "cortex.rules.render.promotion_line",
      "note": "#173 the fixed sentence, no provider call",
      "children": []
     },
     {
      "name": "cortex.events.emitter.EventEmitter.emit",
      "note": "#173 the house seam: a bus failure is a log line, never a lost promotion",
      "children": []
     }
    ]
   },
   {
    "name": "cortex.agentic.loop.AgentLoop.stream_chat",
    "note": "#173 takes one pending line and yields it as the turn's first text delta",
    "children": [
     {
      "name": "cortex.agentic.notices.PromotionNotices.take",
      "note": "#173 FIFO, one per turn, None when nothing is pending",
      "children": []
     }
    ]
   },
   {
    "name": "cortex.api.routes.chat.chat_stream",
    "note": "#172 the same, on the ResponseDoneEvent it already matches",
    "children": [
     {
      "name": "cortex.rules.service.RuleService.list_active",
      "note": "#172 the same snapshot the non-streaming route takes",
      "children": []
     },
     {
      "name": "cortex.rules.queue.enqueue_turn",
      "note": "#172 unchanged",
      "children": []
     }
    ]
   }
  ]
 },
 "file_tree": [
  {
   "kind": "add",
   "path": "src/cortex/rules/feedback.py",
   "note": "#168 Feedback, AttributedUse, RuleProposal, FeedbackClassifier, _parse_feedback"
  },
  {
   "kind": "add",
   "path": "src/cortex/rules/attribution.py",
   "note": "#168 AttributionRun"
  },
  {
   "kind": "mod",
   "path": "src/cortex/llm/providers/openai.py",
   "note": "#168 chat() carries the reasoning into the content"
  },
  {
   "kind": "add",
   "path": "tests/unit/rules/test_feedback.py",
   "note": "#168 the classifier and the parse"
  },
  {
   "kind": "add",
   "path": "tests/unit/rules/test_attribution.py",
   "note": "#168 the run against the in-memory repository"
  },
  {
   "kind": "mod",
   "path": "tests/unit/test_llm.py",
   "note": "#168 the non-streaming reasoning tests"
  },
  {
   "kind": "add",
   "path": "migrations/011_queued_feedback.sql",
   "note": "#169 the queue table and rule_uses.feedback_id with its partial unique index"
  },
  {
   "kind": "add",
   "path": "src/cortex/rules/queue.py",
   "note": "#169 QueuedFeedback, QueuedFeedbackStatus, FeedbackQueue, PostgresFeedbackQueue, FeedbackDrain"
  },
  {
   "kind": "mod",
   "path": "src/cortex/rules/interfaces.py",
   "note": "#169 the FeedbackQueue port"
  },
  {
   "kind": "mod",
   "path": "src/cortex/rules/models.py",
   "note": "#169 RuleUse.feedback_id, nullable"
  },
  {
   "kind": "mod",
   "path": "src/cortex/rules/repository.py",
   "note": "#169 write and read rule_uses.feedback_id"
  },
  {
   "kind": "add",
   "path": "tests/unit/rules/test_queue.py",
   "note": "#169 the statements and the drain"
  },
  {
   "kind": "add",
   "path": "src/cortex/rules/conflicts.py",
   "note": "#170 the conflict check against the active set"
  },
  {
   "kind": "mod",
   "path": "src/cortex/rules/attribution.py",
   "note": "#170 the run applies the proposal after the conflict check"
  },
  {
   "kind": "add",
   "path": "tests/unit/rules/test_conflicts.py",
   "note": "#170 the check and the proposal path"
  },
  {
   "kind": "mod",
   "path": "tests/unit/rules/test_attribution.py",
   "note": "#170 the proposal path through the run"
  },
  {
   "kind": "add",
   "path": "src/cortex/rules/splits.py",
   "note": "#171 SplitProposal, _parse_split, SplitProposer"
  },
  {
   "kind": "mod",
   "path": "src/cortex/rules/attribution.py",
   "note": "#171 the run splits a rule whose scope correction was attributed"
  },
  {
   "kind": "mod",
   "path": "src/cortex/rules/service.py",
   "note": "#171 list_uses on the published interface; split leaves both children in staging when the parent has no use"
  },
  {
   "kind": "add",
   "path": "tests/unit/rules/test_splits.py",
   "note": "#171 the proposer and its parse"
  },
  {
   "kind": "mod",
   "path": "tests/unit/rules/test_attribution.py",
   "note": "#171 the split path through the run"
  },
  {
   "kind": "mod",
   "path": "tests/unit/rules/test_service.py",
   "note": "#171 a split with no uses leaves both children in staging"
  },
  {
   "kind": "mod",
   "path": "src/cortex/rules/queue.py",
   "note": "#172 enqueue_turn: the turn's rules in, one pending row or none out, never an exception"
  },
  {
   "kind": "mod",
   "path": "src/cortex/api/routes/chat.py",
   "note": "#172 both endpoints snapshot the rules before the turn and enqueue after the answer"
  },
  {
   "kind": "mod",
   "path": "src/cortex/api/dependencies.py",
   "note": "#172 get_rule_service and get_feedback_queue"
  },
  {
   "kind": "mod",
   "path": "src/cortex/main.py",
   "note": "#172 builds the run, the queue and the drain and starts the drain loop"
  },
  {
   "kind": "mod",
   "path": "tests/unit/rules/test_queue.py",
   "note": "#172 the helper's four exits"
  },
  {
   "kind": "mod",
   "path": "tests/unit/api/test_chat_stream.py",
   "note": "#172 the streaming route enqueues one row"
  },
  {
   "kind": "add",
   "path": "tests/unit/api/test_chat_attribution.py",
   "note": "#172 the non-streaming route"
  },
  {
   "kind": "add",
   "path": "tests/unit/test_main_wiring.py",
   "note": "#172 the composition root and the drain loop"
  },
  {
   "kind": "add",
   "path": "tests/e2e/test_rule_attribution.py",
   "note": "#172 a correction in chat moves a rule's confidence, end to end"
  },
  {
   "kind": "mod",
   "path": "src/cortex/rules/render.py",
   "note": "#173 promotion_line lives beside render_rules: the sentence the user reads"
  },
  {
   "kind": "mod",
   "path": "src/cortex/rules/service.py",
   "note": "#173 an optional EventEmitter; one helper emits rule.promoted after a staging -> definitive refresh"
  },
  {
   "kind": "mod",
   "path": "src/cortex/events/types.py",
   "note": "#173 RULE_PROMOTED = rule.promoted"
  },
  {
   "kind": "add",
   "path": "src/cortex/agentic/notices.py",
   "note": "#173 PromotionNotices: the agentic side of the edge, bounded FIFO"
  },
  {
   "kind": "mod",
   "path": "src/cortex/agentic/loop.py",
   "note": "#173 AgentLoop takes one line per chat turn and yields it as the first text delta"
  },
  {
   "kind": "mod",
   "path": "src/cortex/main.py",
   "note": "#173 builds and subscribes the notices, gives the service its emitter"
  },
  {
   "kind": "add",
   "path": "tests/unit/rules/test_promotion.py",
   "note": "#173 the sentence"
  },
  {
   "kind": "mod",
   "path": "tests/unit/rules/test_service.py",
   "note": "#173 the emission: one after commit, none for a demotion or an archive"
  },
  {
   "kind": "add",
   "path": "tests/unit/agentic/test_notices.py",
   "note": "#173 the FIFO buffer and its cap"
  },
  {
   "kind": "mod",
   "path": "tests/unit/agentic/test_loop.py",
   "note": "#173 the line as the turn's first delta, once"
  },
  {
   "kind": "mod",
   "path": "tests/unit/test_main_wiring.py",
   "note": "#173 the root wires the emitter and the notices"
  },
  {
   "kind": "mod",
   "path": "src/cortex/rules/feedback.py",
   "note": "#174 a proposal may name the rule it replaces; the prompt asks for it and the parse drops an unknown id"
  },
  {
   "kind": "mod",
   "path": "src/cortex/rules/service.py",
   "note": "#174 create_dictated replaces: validate the predecessor, create the successor, archive with superseded_by"
  },
  {
   "kind": "mod",
   "path": "src/cortex/rules/attribution.py",
   "note": "#174 a dictated proposal hands its replaces to the service"
  },
  {
   "kind": "mod",
   "path": "tests/unit/rules/test_feedback.py",
   "note": "#174 the replaces parse"
  },
  {
   "kind": "mod",
   "path": "tests/unit/rules/test_service.py",
   "note": "#174 the replacement: no inherited uses, the predecessor archived and linked"
  },
  {
   "kind": "mod",
   "path": "tests/unit/rules/test_attribution.py",
   "note": "#174 the run passes replaces for a dictation only"
  },
  {
   "kind": "mod",
   "path": "tests/unit/rules/test_cli_rules.py",
   "note": "#174 rules:show lists both directions"
  },
  {
   "kind": "mod",
   "path": "src/cortex/rules/attribution.py",
   "note": "#172 advisory from #171: _apply_proposal extracted out of attribute, behaviour identical"
  },
  {
   "kind": "mod",
   "path": "src/cortex/rules/feedback.py",
   "note": "#172 advisory from #171: _parse_spec is the single spec parser, used by the proposal parser"
  },
  {
   "kind": "mod",
   "path": "src/cortex/rules/splits.py",
   "note": "#172 advisory from #171: _parse_child delegates to _parse_spec, refusal messages unchanged"
  },
  {
   "kind": "mod",
   "path": "tests/unit/rules/fakes.py",
   "note": "#172 advisory from #171: the LLM test stubs (llm_returning, sent_text) hoisted here"
  },
  {
   "kind": "mod",
   "path": "tests/unit/rules/test_feedback.py",
   "note": "#172 advisory from #171: uses the shared stub"
  },
  {
   "kind": "mod",
   "path": "tests/unit/rules/test_conflicts.py",
   "note": "#172 advisory from #171: uses the shared stub"
  },
  {
   "kind": "mod",
   "path": "tests/unit/rules/test_splits.py",
   "note": "#172 advisory from #171: uses the shared stub"
  }
 ],
 "signatures": [
  {
   "module": "src/cortex/rules/feedback.py",
   "language": "python",
   "code": "@dataclass(frozen=True)\nclass AttributedUse:\n    rule_id: UUID\n    outcome: UseOutcome\n\n@dataclass(frozen=True)\nclass RuleProposal:\n    spec: RuleSpec\n    origin: RuleOrigin\n\n@dataclass(frozen=True)\nclass Feedback:\n    uses: list[AttributedUse] = field(default_factory=list)\n    proposals: list[RuleProposal] = field(default_factory=list)\n\ndef _parse_feedback(content: str, known_rule_ids: set[UUID]) -> Feedback: ...\n\nclass FeedbackClassifier:\n    def __init__(self, llm_client: LLMClient) -> None: ...\n    async def classify(self, rules: Sequence[Rule], assistant_turn: str, user_message: str) -> Feedback: ..."
  },
  {
   "module": "src/cortex/rules/attribution.py",
   "language": "python",
   "code": "class AttributionRun:\n    def __init__(self, classifier: FeedbackClassifier, service: RuleService) -> None: ...\n    async def attribute(self, rules: Sequence[Rule], session_id: UUID, assistant_turn: str, user_message: str, situation: str, feedback_id: UUID | None = None) -> list[Rule] | None: ..."
  },
  {
   "module": "src/cortex/llm/providers/openai.py",
   "language": "python",
   "code": "async def chat(self, messages: list[ChatMessage], *, tools: list[ToolDefinition] | None = None, generation_config: GenerationConfig | None = None) -> ChatResult: ..."
  },
  {
   "module": "src/cortex/rules/service.py",
   "language": "python",
   "code": "async def record_use(self, rule_id: UUID, session_id: UUID, situation: str, outcome: UseOutcome, feedback_id: UUID | None = None) -> Rule: ..."
  },
  {
   "module": "src/cortex/llm/providers/openai.py",
   "language": "python",
   "code": "def _reasoning_delta(delta: Any) -> str | None: ..."
  },
  {
   "module": "src/cortex/rules/queue.py",
   "language": "python",
   "code": "class QueuedFeedbackStatus(StrEnum):\n    PENDING = \"pending\"\n    ATTRIBUTED = \"attributed\"\n    DROPPED = \"dropped\"\n\n@dataclass(frozen=True)\nclass QueuedFeedback:\n    session_id: UUID\n    rule_ids: tuple[UUID, ...]\n    assistant_turn: str\n    user_reply: str\n    status: QueuedFeedbackStatus = QueuedFeedbackStatus.PENDING\n    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))\n    attributed_at: datetime | None = None\n    id: UUID | None = None\n\nclass FeedbackQueue(ABC):\n    @abstractmethod\n    async def enqueue(self, item: QueuedFeedback) -> QueuedFeedback: ...\n    @abstractmethod\n    async def list_pending(self, limit: int = 10) -> list[QueuedFeedback]: ...\n    @abstractmethod\n    async def mark_attributed(self, feedback_id: UUID, now: datetime) -> None: ...\n    @abstractmethod\n    async def drop_stale(self, cutoff: datetime) -> int: ...\n\nclass PostgresFeedbackQueue(FeedbackQueue):\n    def __init__(self, session_factory: Callable[[], AbstractAsyncContextManager[Any]] = DbSession) -> None: ...\n\nclass FeedbackDrain:\n    def __init__(self, queue: FeedbackQueue, run: AttributionRun, service: RuleService, *, clock: Callable[[], datetime] = _utcnow) -> None: ...\n    async def drain(self, limit: int = 10) -> int: ..."
  },
  {
   "module": "src/cortex/rules/service.py",
   "language": "python",
   "code": "async def get(self, rule_id: UUID) -> Rule | None: ..."
  },
  {
   "module": "src/cortex/rules/conflicts.py",
   "language": "python",
   "code": "class ConflictCheck:\n    def __init__(self, llm_client: LLMClient) -> None: ...\n    async def contradicts(self, candidate: RuleSpec, active: Sequence[Rule]) -> bool: ..."
  },
  {
   "module": "src/cortex/rules/attribution.py",
   "language": "python",
   "code": "class AttributionRun:\n    def __init__(self, classifier: FeedbackClassifier, service: RuleService, conflicts: ConflictCheck | None = None, splits: SplitProposer | None = None) -> None: ...\n    async def attribute(self, rules: Sequence[Rule], session_id: UUID, assistant_turn: str, user_message: str, situation: str, feedback_id: UUID | None = None) -> list[Rule] | None: ..."
  },
  {
   "module": "src/cortex/rules/service.py",
   "language": "python",
   "code": "async def list_active(self) -> list[Rule]: ...\nasync def create_dictated(self, session_id: UUID, spec: RuleSpec) -> Rule: ...\nasync def create_from_correction(self, spec: RuleSpec) -> Rule: ..."
  },
  {
   "module": "src/cortex/rules/splits.py",
   "language": "python",
   "code": "@dataclass(frozen=True)\nclass SplitProposal:\n    children: tuple[RuleSpec, RuleSpec]\n    assignment: tuple[int, ...]\n\ndef _parse_split(content: str, use_count: int) -> SplitProposal: ...\n\nclass SplitProposer:\n    def __init__(self, llm_client: LLMClient) -> None: ...\n    async def propose(self, rule: Rule, uses: Sequence[RuleUse]) -> SplitProposal: ..."
  },
  {
   "module": "src/cortex/rules/service.py",
   "language": "python",
   "code": "async def list_uses(self, rule_id: UUID) -> list[RuleUse]: ..."
  },
  {
   "module": "src/cortex/rules/service.py",
   "language": "python",
   "code": "async def split(self, rule_id: UUID, children: Sequence[RuleSpec], assignments: Mapping[int, int]) -> list[Rule]: ..."
  },
  {
   "module": "src/cortex/rules/queue.py",
   "language": "python",
   "code": "async def enqueue_turn(queue: FeedbackQueue, rules: Sequence[Rule], session_id: UUID, assistant_turn: str, user_reply: str) -> QueuedFeedback | None: ..."
  },
  {
   "module": "src/cortex/api/routes/chat.py",
   "language": "python",
   "code": "async def chat(request: ChatRequest, key: str = Depends(get_api_key), interaction_service: InteractionService = Depends(get_interaction_service), execution_module: ExecutionModule = Depends(get_execution_module), rule_service: RuleService = Depends(get_rule_service), feedback_queue: FeedbackQueue = Depends(get_feedback_queue)) -> ChatResponse: ..."
  },
  {
   "module": "src/cortex/rules/render.py",
   "language": "python",
   "code": "def promotion_line(rule: Rule) -> str: ..."
  },
  {
   "module": "src/cortex/rules/feedback.py",
   "language": "python",
   "code": "@dataclass(frozen=True)\nclass RuleProposal:\n    spec: RuleSpec\n    origin: RuleOrigin\n    replaces: UUID | None = None"
  },
  {
   "module": "src/cortex/rules/service.py",
   "language": "python",
   "code": "async def create_dictated(self, session_id: UUID, spec: RuleSpec, *, replaces: UUID | None = None) -> Rule: ..."
  },
  {
   "module": "src/cortex/rules/attribution.py",
   "language": "python",
   "code": "async def _apply_proposal(self, proposals: list[RuleProposal], session_id: UUID) -> list[Rule]: ..."
  },
  {
   "module": "src/cortex/agentic/loop.py",
   "language": "python",
   "code": "class AgentLoop:\n    def __init__(self, context_builder: ContextBuilder, reasoner: Reasoner, executor: LoopExecutor, event_bus: EventBus | None = None, session_repository: SessionRepository | None = None, max_chat_iterations: int = 20, max_goal_iterations: int = 100, now: Callable[[], float] = time.monotonic, notices: PromotionNotices | None = None): ...\n    async def stream_chat(self, session_id: UUID, user_message: str, *, max_iterations: int | None = None, stream: bool = False) -> AsyncGenerator[LoopEvent, None]: ..."
  },
  {
   "module": "src/cortex/events/emitter.py",
   "language": "python",
   "code": "async def emit(self, event_type: str, payload: dict[str, Any] | None = None, *, session_id: UUID | None = None, trace_id: str | None = None, salience: float = 0.5) -> None: ..."
  },
  {
   "module": "src/cortex/rules/service.py",
   "language": "python",
   "code": "class RuleService:\n    def __init__(self, repository: RuleRepository, *, clock: Callable[[], datetime] = _utcnow, emitter: EventEmitter | None = None) -> None: ..."
  },
  {
   "module": "src/cortex/agentic/notices.py",
   "language": "python",
   "code": "class PromotionNotices:\n    async def handle(self, event: BaseEvent) -> None: ...\n    def take(self) -> str | None: ..."
  },
  {
   "module": "src/cortex/api/routes/chat.py",
   "language": "python",
   "code": "async def chat_stream(request: ChatRequest, key: str = Depends(get_api_key), interaction_service: InteractionService = Depends(get_interaction_service), execution_module: ExecutionModule = Depends(get_execution_module), rule_service: RuleService = Depends(get_rule_service), feedback_queue: FeedbackQueue = Depends(get_feedback_queue)) -> StreamingResponse: ..."
  }
 ],
 "data_flow": {
  "nodes": [
   {
    "id": "cortex.rules.attribution.AttributionRun.attribute",
    "module": "src/cortex/rules/attribution.py",
    "kind": "new"
   },
   {
    "id": "cortex.rules.feedback.FeedbackClassifier.classify",
    "module": "src/cortex/rules/feedback.py",
    "kind": "new"
   },
   {
    "id": "cortex.rules.feedback._parse_feedback",
    "module": "src/cortex/rules/feedback.py",
    "kind": "new"
   },
   {
    "id": "cortex.rules.service.RuleService.record_use",
    "module": "src/cortex/rules/service.py",
    "kind": "keep"
   },
   {
    "id": "cortex.llm.providers.openai.OpenAIClient.chat",
    "module": "src/cortex/llm/providers/openai.py",
    "kind": "mod"
   },
   {
    "id": "cortex.llm.providers.openai._reasoning_delta",
    "module": "src/cortex/llm/providers/openai.py",
    "kind": "keep"
   },
   {
    "id": "cortex.rules.queue.FeedbackDrain.drain",
    "module": "src/cortex/rules/queue.py",
    "kind": "new"
   },
   {
    "id": "cortex.rules.queue.FeedbackQueue.list_pending",
    "module": "src/cortex/rules/queue.py",
    "kind": "new"
   },
   {
    "id": "cortex.rules.queue.FeedbackQueue.mark_attributed",
    "module": "src/cortex/rules/queue.py",
    "kind": "new"
   },
   {
    "id": "cortex.rules.queue.FeedbackQueue.drop_stale",
    "module": "src/cortex/rules/queue.py",
    "kind": "new"
   },
   {
    "id": "cortex.rules.service.RuleService.get",
    "module": "src/cortex/rules/service.py",
    "kind": "keep"
   },
   {
    "id": "cortex.rules.conflicts.ConflictCheck.contradicts",
    "module": "src/cortex/rules/conflicts.py",
    "kind": "new"
   },
   {
    "id": "cortex.rules.service.RuleService.list_active",
    "module": "src/cortex/rules/service.py",
    "kind": "keep"
   },
   {
    "id": "cortex.rules.service.RuleService.create_dictated",
    "module": "src/cortex/rules/service.py",
    "kind": "keep"
   },
   {
    "id": "cortex.rules.service.RuleService.create_from_correction",
    "module": "src/cortex/rules/service.py",
    "kind": "keep"
   },
   {
    "id": "cortex.rules.splits.SplitProposer.propose",
    "module": "src/cortex/rules/splits.py",
    "kind": "add"
   },
   {
    "id": "cortex.rules.splits._parse_split",
    "module": "src/cortex/rules/splits.py",
    "kind": "add"
   },
   {
    "id": "cortex.rules.service.RuleService.list_uses",
    "module": "src/cortex/rules/service.py",
    "kind": "add"
   },
   {
    "id": "cortex.rules.service.RuleService.split",
    "module": "src/cortex/rules/service.py",
    "kind": "keep"
   },
   {
    "id": "cortex.rules.queue.enqueue_turn",
    "module": "src/cortex/rules/queue.py",
    "kind": "add"
   },
   {
    "id": "cortex.api.routes.chat.chat",
    "module": "src/cortex/api/routes/chat.py",
    "kind": "mod"
   },
   {
    "id": "cortex.api.routes.chat.chat_stream",
    "module": "src/cortex/api/routes/chat.py",
    "kind": "mod"
   },
   {
    "id": "cortex.rules.queue.FeedbackQueue.enqueue",
    "module": "src/cortex/rules/queue.py",
    "kind": "keep"
   },
   {
    "id": "cortex.rules.service.RuleService.record_use",
    "module": "src/cortex/rules/service.py",
    "kind": "keep"
   },
   {
    "id": "cortex.events.emitter.EventEmitter.emit",
    "module": "src/cortex/events/emitter.py",
    "kind": "keep"
   },
   {
    "id": "cortex.agentic.notices.PromotionNotices.take",
    "module": "src/cortex/agentic/notices.py",
    "kind": "add"
   },
   {
    "id": "cortex.rules.attribution.AttributionRun._apply_proposal",
    "module": "src/cortex/rules/attribution.py",
    "kind": "mod"
   },
   {
    "id": "cortex.rules.service.RuleService.create_dictated",
    "module": "src/cortex/rules/service.py",
    "kind": "mod"
   },
   {
    "id": "cortex.agentic.loop.AgentLoop.stream_chat",
    "module": "src/cortex/agentic/loop.py",
    "kind": "mod"
   }
  ],
  "edges": [
   {
    "from": "cortex.rules.attribution.AttributionRun.attribute",
    "to": "cortex.rules.feedback.FeedbackClassifier.classify"
   },
   {
    "from": "cortex.rules.attribution.AttributionRun.attribute",
    "to": "cortex.rules.service.RuleService.record_use"
   },
   {
    "from": "cortex.rules.feedback.FeedbackClassifier.classify",
    "to": "cortex.rules.feedback._parse_feedback"
   },
   {
    "from": "cortex.rules.feedback.FeedbackClassifier.classify",
    "to": "cortex.llm.providers.openai.OpenAIClient.chat"
   },
   {
    "from": "cortex.llm.providers.openai.OpenAIClient.chat",
    "to": "cortex.llm.providers.openai._reasoning_delta"
   },
   {
    "from": "cortex.rules.queue.FeedbackDrain.drain",
    "to": "cortex.rules.queue.FeedbackQueue.list_pending"
   },
   {
    "from": "cortex.rules.queue.FeedbackDrain.drain",
    "to": "cortex.rules.service.RuleService.get"
   },
   {
    "from": "cortex.rules.queue.FeedbackDrain.drain",
    "to": "cortex.rules.attribution.AttributionRun.attribute"
   },
   {
    "from": "cortex.rules.queue.FeedbackDrain.drain",
    "to": "cortex.rules.queue.FeedbackQueue.mark_attributed"
   },
   {
    "from": "cortex.rules.queue.FeedbackDrain.drain",
    "to": "cortex.rules.queue.FeedbackQueue.drop_stale"
   },
   {
    "from": "cortex.rules.attribution.AttributionRun.attribute",
    "to": "cortex.rules.conflicts.ConflictCheck.contradicts"
   },
   {
    "from": "cortex.rules.attribution.AttributionRun.attribute",
    "to": "cortex.rules.service.RuleService.record_use"
   },
   {
    "from": "cortex.rules.attribution.AttributionRun.attribute",
    "to": "cortex.rules.service.RuleService.list_active"
   },
   {
    "from": "cortex.rules.attribution.AttributionRun.attribute",
    "to": "cortex.rules.service.RuleService.create_dictated"
   },
   {
    "from": "cortex.rules.attribution.AttributionRun.attribute",
    "to": "cortex.rules.service.RuleService.create_from_correction"
   },
   {
    "from": "cortex.rules.attribution.AttributionRun.attribute",
    "to": "cortex.rules.splits.SplitProposer.propose"
   },
   {
    "from": "cortex.rules.splits.SplitProposer.propose",
    "to": "cortex.llm.providers.openai.OpenAIClient.chat"
   },
   {
    "from": "cortex.rules.splits.SplitProposer.propose",
    "to": "cortex.rules.splits._parse_split"
   },
   {
    "from": "cortex.rules.attribution.AttributionRun.attribute",
    "to": "cortex.rules.service.RuleService.list_uses"
   },
   {
    "from": "cortex.rules.attribution.AttributionRun.attribute",
    "to": "cortex.rules.service.RuleService.split"
   },
   {
    "from": "cortex.api.routes.chat.chat",
    "to": "cortex.rules.service.RuleService.list_active"
   },
   {
    "from": "cortex.api.routes.chat.chat",
    "to": "cortex.rules.queue.enqueue_turn"
   },
   {
    "from": "cortex.api.routes.chat.chat_stream",
    "to": "cortex.rules.service.RuleService.list_active"
   },
   {
    "from": "cortex.api.routes.chat.chat_stream",
    "to": "cortex.rules.queue.enqueue_turn"
   },
   {
    "from": "cortex.rules.queue.enqueue_turn",
    "to": "cortex.rules.queue.FeedbackQueue.enqueue"
   },
   {
    "from": "cortex.rules.service.RuleService.record_use",
    "to": "cortex.events.emitter.EventEmitter.emit"
   },
   {
    "from": "cortex.agentic.loop.AgentLoop.stream_chat",
    "to": "cortex.agentic.notices.PromotionNotices.take"
   },
   {
    "from": "cortex.rules.attribution.AttributionRun._apply_proposal",
    "to": "cortex.rules.service.RuleService.create_dictated"
   }
  ]
 },
 "contracts": [
  {
   "site": "cortex.rules.attribution.AttributionRun.attribute",
   "callee": "cortex.rules.feedback.FeedbackClassifier.classify",
   "exits": [
    {
     "kind": "returns",
     "type": "Feedback: the attributed uses and at most one proposal",
     "handled_at": "each use goes to RuleService.record_use; the proposal is not acted on in this ticket"
    },
    {
     "kind": "absent",
     "type": "None: the classification failed and wrote nothing",
     "handled_at": "the caller sees None, leaves the queued row pending and retries it later"
    },
    {
     "kind": "raises",
     "type": "ValueError on malformed provider output, provider and circuit-breaker errors",
     "handled_at": "caught here by one handler, logged, nothing written, returns None so a queued row is not consumed"
    },
    {
     "kind": "effect",
     "type": "one billable provider call",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.feedback.FeedbackClassifier.classify",
   "callee": "cortex.llm.providers.openai.OpenAIClient.chat",
   "exits": [
    {
     "kind": "returns",
     "type": "ChatResult whose message content is the JSON the prompt asked for",
     "handled_at": "handed to _parse_feedback"
    },
    {
     "kind": "absent",
     "type": "content None or blank",
     "handled_at": "_parse_feedback raises ValueError on it"
    },
    {
     "kind": "raises",
     "type": "the provider's error, or CircuitOpenError from the breaker",
     "handled_at": "not caught here: AttributionRun.attribute catches and writes nothing"
    },
    {
     "kind": "effect",
     "type": "one provider call, temperature 0",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.attribution.AttributionRun.attribute",
   "callee": "cortex.rules.service.RuleService.record_use",
   "exits": [
    {
     "kind": "returns",
     "type": "Rule with the recomputed confidence and state",
     "handled_at": "collected into the returned list"
    },
    {
     "kind": "absent",
     "type": "RuleNotFoundError when the rule vanished between classify and write",
     "handled_at": "caught by the single handler, logged with the rule id, the remaining uses are still written"
    },
    {
     "kind": "raises",
     "type": "the repository's error (asyncpg) from the write",
     "handled_at": "caught by the same handler, logged, the remaining uses are still written"
    },
    {
     "kind": "effect",
     "type": "one rule_uses row plus one rules refresh per use",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.feedback.FeedbackClassifier.classify",
   "callee": "cortex.rules.feedback._parse_feedback",
   "exits": [
    {
     "kind": "returns",
     "type": "Feedback with only the uses and proposals that parse and name a known rule",
     "handled_at": "returned as is"
    },
    {
     "kind": "absent",
     "type": "an empty Feedback when every entry was dropped",
     "handled_at": "returned as is, AttributionRun writes nothing"
    },
    {
     "kind": "raises",
     "type": "ValueError on malformed JSON or a missing uses key",
     "handled_at": "propagates to AttributionRun.attribute, which logs it and writes nothing"
    },
    {
     "kind": "effect",
     "type": "none, pure",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.llm.providers.openai.OpenAIClient.chat",
   "callee": "cortex.llm.providers.openai._reasoning_delta",
   "exits": [
    {
     "kind": "returns",
     "type": "the reasoning text of the delta",
     "handled_at": "wrapped into one inline think block in the content"
    },
    {
     "kind": "absent",
     "type": "None or empty for a provider/model that publishes no reasoning",
     "handled_at": "content left untouched"
    },
    {
     "kind": "raises",
     "type": "nothing expected: it reads attributes off a dict-like delta",
     "handled_at": "\u2014 nothing raised, nothing to handle"
    },
    {
     "kind": "effect",
     "type": "none, pure",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.queue.FeedbackDrain.drain",
   "callee": "cortex.rules.queue.FeedbackQueue.list_pending",
   "exits": [
    {
     "kind": "returns",
     "type": "the pending rows, oldest first",
     "handled_at": "the drain loop"
    },
    {
     "kind": "absent",
     "type": "an empty list: nothing to attribute",
     "handled_at": "no work that pass"
    },
    {
     "kind": "raises",
     "type": "the repository's error (asyncpg)",
     "handled_at": "logged, the row stays pending, the drain continues"
    },
    {
     "kind": "effect",
     "type": "one read-only query",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.queue.FeedbackDrain.drain",
   "callee": "cortex.rules.service.RuleService.get",
   "exits": [
    {
     "kind": "returns",
     "type": "the Rule, or None when it has been deleted since",
     "handled_at": "the drain loop"
    },
    {
     "kind": "absent",
     "type": "None: the id is skipped, the rest of the row is still processed",
     "handled_at": "no work that pass"
    },
    {
     "kind": "raises",
     "type": "the repository's error",
     "handled_at": "logged, the row stays pending, the drain continues"
    },
    {
     "kind": "effect",
     "type": "one read per rule id",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.queue.FeedbackDrain.drain",
   "callee": "cortex.rules.attribution.AttributionRun.attribute",
   "exits": [
    {
     "kind": "returns",
     "type": "the refreshed rules, or None when the classification failed",
     "handled_at": "the drain loop"
    },
    {
     "kind": "absent",
     "type": "None: the row stays pending and a later pass retries it",
     "handled_at": "no work that pass"
    },
    {
     "kind": "raises",
     "type": "nothing: the run catches its own failures",
     "handled_at": "logged, the row stays pending, the drain continues"
    },
    {
     "kind": "effect",
     "type": "one provider call per pending row that still has rules",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.queue.FeedbackDrain.drain",
   "callee": "cortex.rules.queue.FeedbackQueue.mark_attributed",
   "exits": [
    {
     "kind": "returns",
     "type": "None, called only when the run returned a list (not None)",
     "handled_at": "the drain loop"
    },
    {
     "kind": "absent",
     "type": "the row was already attributed by another drain: a no-op update",
     "handled_at": "no work that pass"
    },
    {
     "kind": "raises",
     "type": "the repository's error, logged; the row stays pending",
     "handled_at": "logged, the row stays pending, the drain continues"
    },
    {
     "kind": "effect",
     "type": "one update",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.queue.FeedbackDrain.drain",
   "callee": "cortex.rules.queue.FeedbackQueue.drop_stale",
   "exits": [
    {
     "kind": "returns",
     "type": "the number of rows dropped",
     "handled_at": "the drain loop"
    },
    {
     "kind": "absent",
     "type": "zero: nothing is old enough",
     "handled_at": "no work that pass"
    },
    {
     "kind": "raises",
     "type": "the repository's error, logged; the pass still reports its attributions",
     "handled_at": "logged, the row stays pending, the drain continues"
    },
    {
     "kind": "effect",
     "type": "one update",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.attribution.AttributionRun.attribute",
   "callee": "cortex.rules.conflicts.ConflictCheck.contradicts",
   "exits": [
    {
     "kind": "returns",
     "type": "True or False from the provider's verdict",
     "handled_at": "True drops the proposal, False lets it be created"
    },
    {
     "kind": "absent",
     "type": "an empty active set: nothing to contradict",
     "handled_at": "False without any provider call, so a first rule costs one call, not two"
    },
    {
     "kind": "raises",
     "type": "the provider's error, or malformed JSON",
     "handled_at": "logged, the proposal is dropped, the uses are still written"
    },
    {
     "kind": "effect",
     "type": "one provider call, and only when there is a proposal",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.attribution.AttributionRun.attribute",
   "callee": "cortex.rules.service.RuleService.list_active",
   "exits": [
    {
     "kind": "returns",
     "type": "the active rules, already capped at the context budget",
     "handled_at": "the proposal is stored from it"
    },
    {
     "kind": "absent",
     "type": "no active rule: the check short-circuits",
     "handled_at": "nothing to create"
    },
    {
     "kind": "raises",
     "type": "the repository's error",
     "handled_at": "caught by the same handler as the conflict check: the proposal is dropped, the uses stand"
    },
    {
     "kind": "effect",
     "type": "one read per sweep",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.attribution.AttributionRun.attribute",
   "callee": "cortex.rules.service.RuleService.create_dictated",
   "exits": [
    {
     "kind": "returns",
     "type": "the new Rule, definitive with the dictation as its first use",
     "handled_at": "the proposal is stored from it"
    },
    {
     "kind": "absent",
     "type": "none: the branch is taken only for a dictated proposal",
     "handled_at": "nothing to create"
    },
    {
     "kind": "raises",
     "type": "ValueError on blank text, the repository's error",
     "handled_at": "caught by the same handler as the conflict check: the proposal is dropped, the uses stand"
    },
    {
     "kind": "effect",
     "type": "one rules row plus one use row",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.attribution.AttributionRun.attribute",
   "callee": "cortex.rules.service.RuleService.create_from_correction",
   "exits": [
    {
     "kind": "returns",
     "type": "the new Rule, staging at the prior",
     "handled_at": "the proposal is stored from it"
    },
    {
     "kind": "absent",
     "type": "none: the branch is taken only for a correction-born proposal",
     "handled_at": "nothing to create"
    },
    {
     "kind": "raises",
     "type": "ValueError on blank text, the repository's error",
     "handled_at": "caught by the same handler as the conflict check: the proposal is dropped, the uses stand"
    },
    {
     "kind": "effect",
     "type": "one rules row",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.attribution.AttributionRun.attribute",
   "callee": "cortex.rules.splits.SplitProposer.propose",
   "exits": [
    {
     "kind": "returns",
     "type": "SplitProposal: two children and one child index per use",
     "handled_at": "handed to RuleService.split with the use ids the run already holds"
    },
    {
     "kind": "absent",
     "type": "none: the proposer always answers or raises, as the classifier does",
     "handled_at": "the only non-answer is an exception, handled below"
    },
    {
     "kind": "raises",
     "type": "ValueError on an unusable answer, the provider's error, CircuitOpenError",
     "handled_at": "logged by the run; the uses stand, the parent stays active, no child is created"
    },
    {
     "kind": "effect",
     "type": "one provider call, and only when a scope correction was attributed",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.splits.SplitProposer.propose",
   "callee": "cortex.llm.providers.openai.OpenAIClient.chat",
   "exits": [
    {
     "kind": "returns",
     "type": "ChatResult whose content is the JSON the prompt asked for",
     "handled_at": "handed to _parse_split"
    },
    {
     "kind": "absent",
     "type": "content None or blank",
     "handled_at": "_parse_split raises ValueError on it"
    },
    {
     "kind": "raises",
     "type": "the provider's error, or CircuitOpenError from the breaker",
     "handled_at": "not caught here: AttributionRun.attribute catches it and writes nothing"
    },
    {
     "kind": "effect",
     "type": "one provider call, temperature 0",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.splits.SplitProposer.propose",
   "callee": "cortex.rules.splits._parse_split",
   "exits": [
    {
     "kind": "returns",
     "type": "SplitProposal with exactly two non-blank children and an assignment of 0/1 of the right length",
     "handled_at": "returned as is, unvalidated against the parent's uses: RuleService.split validates"
    },
    {
     "kind": "absent",
     "type": "no partial proposal: a reply that cannot yield two children is not a proposal",
     "handled_at": "raised instead, see below"
    },
    {
     "kind": "raises",
     "type": "ValueError on malformed JSON, on a missing or extra child, on a blank condition/action, on an assignment that is not a list of 0/1",
     "handled_at": "propagates to AttributionRun.attribute, which logs it and leaves the parent active"
    },
    {
     "kind": "effect",
     "type": "none, pure",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.attribution.AttributionRun.attribute",
   "callee": "cortex.rules.service.RuleService.list_uses",
   "exits": [
    {
     "kind": "returns",
     "type": "the parent's uses, oldest first, including the one just recorded",
     "handled_at": "put into the proposer's prompt, one line per use"
    },
    {
     "kind": "absent",
     "type": "an empty list: the parent has no use to redistribute",
     "handled_at": "the proposer is still asked (a rule can be split without evidence) and the split leaves both children in staging"
    },
    {
     "kind": "raises",
     "type": "the repository's error",
     "handled_at": "logged by the same handler: no proposer call, the recorded use stands"
    },
    {
     "kind": "effect",
     "type": "one read per scope correction",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.attribution.AttributionRun.attribute",
   "callee": "cortex.rules.service.RuleService.split",
   "exits": [
    {
     "kind": "returns",
     "type": "the two new children, the parent archived with superseded_by set",
     "handled_at": "collected into the returned list beside the parent"
    },
    {
     "kind": "absent",
     "type": "RuleNotFoundError when the rule vanished between the use and the split",
     "handled_at": "caught by the single handler, logged; nothing is created"
    },
    {
     "kind": "raises",
     "type": "ValueError on an unassigned or out-of-range use before the first write, the repository's error part-way (the split is not transactional)",
     "handled_at": "caught by the same handler, logged; the uses stand and the operator re-runs the split"
    },
    {
     "kind": "effect",
     "type": "two rules rows, one use reassignment per use, one refresh of the parent",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.api.routes.chat.chat",
   "callee": "cortex.rules.service.RuleService.list_active",
   "exits": [
    {
     "kind": "returns",
     "type": "the active rules at the turn's start — the snapshot the row carries",
     "handled_at": "kept beside the turn; passed to enqueue_turn after the answer"
    },
    {
     "kind": "absent",
     "type": "an empty list",
     "handled_at": "enqueue_turn writes nothing: a turn with no rule has nothing to attribute"
    },
    {
     "kind": "raises",
     "type": "the repository's error (database unreachable)",
     "handled_at": "caught by `_snapshot_rules`, the helper both endpoints use: logged, the snapshot stays empty, the row is simply not written and the turn continues"
    },
    {
     "kind": "effect",
     "type": "one read plus the sweep it applies",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.api.routes.chat.chat",
   "callee": "cortex.rules.queue.enqueue_turn",
   "exits": [
    {
     "kind": "returns",
     "type": "the pending row, after the answer",
     "handled_at": "dropped: the response was already decided"
    },
    {
     "kind": "absent",
     "type": "None when the snapshot was empty, the reply blank, or the store failed",
     "handled_at": "nothing to do; the turn is over"
    },
    {
     "kind": "raises",
     "type": "nothing: the helper logs and swallows",
     "handled_at": "nothing to dispatch: the helper already handled it"
    },
    {
     "kind": "effect",
     "type": "at most one INSERT, after the response text is complete",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.api.routes.chat.chat_stream",
   "callee": "cortex.rules.service.RuleService.list_active",
   "exits": [
    {
     "kind": "returns",
     "type": "the active rules when the stream starts — the snapshot the row carries",
     "handled_at": "kept in the generator; passed to enqueue_turn on the done frame"
    },
    {
     "kind": "absent",
     "type": "an empty list",
     "handled_at": "enqueue_turn writes nothing"
    },
    {
     "kind": "raises",
     "type": "the repository's error (database unreachable)",
     "handled_at": "caught by `_snapshot_rules`: logged, empty snapshot, the stream is unaffected"
    },
    {
     "kind": "effect",
     "type": "one read plus the sweep it applies, once per stream",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.api.routes.chat.chat_stream",
   "callee": "cortex.rules.queue.enqueue_turn",
   "exits": [
    {
     "kind": "returns",
     "type": "the pending row, on the ResponseDoneEvent frame",
     "handled_at": "dropped: the done frame is already built"
    },
    {
     "kind": "absent",
     "type": "None on an empty snapshot, a blank reply or a store failure; never reached on an error frame or a cancelled stream",
     "handled_at": "nothing to do"
    },
    {
     "kind": "raises",
     "type": "nothing: the helper logs and swallows",
     "handled_at": "nothing to dispatch: the helper already handled it"
    },
    {
     "kind": "effect",
     "type": "at most one INSERT per completed turn",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.queue.enqueue_turn",
   "callee": "cortex.rules.queue.FeedbackQueue.enqueue",
   "exits": [
    {
     "kind": "returns",
     "type": "the stored row with its id",
     "handled_at": "returned to the caller"
    },
    {
     "kind": "absent",
     "type": "none: enqueue always stores or raises",
     "handled_at": "no such exit: the port stores or raises, so only the two above are dispatched"
    },
    {
     "kind": "raises",
     "type": "ValueError on a blank reply (the guard), the repository's error",
     "handled_at": "caught by one handler, logged, returns None"
    },
    {
     "kind": "effect",
     "type": "one queued_feedback row",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.service.RuleService.record_use",
   "callee": "cortex.events.emitter.EventEmitter.emit",
   "exits": [
    {
     "kind": "returns",
     "type": "None once the bus accepted the event, after the refresh committed",
     "handled_at": "the caller ignores it: the promotion is already stored"
    },
    {
     "kind": "absent",
     "type": "None when the service has no emitter (the CLI, every caller that builds the service without one)",
     "handled_at": "_announce_promotion returns before the call; the rule is still promoted"
    },
    {
     "kind": "raises",
     "type": "nothing: EventEmitter catches the bus error, logs a warning and returns",
     "handled_at": "the emitter itself; a lost line never costs the promotion (AC 3)"
    },
    {
     "kind": "effect",
     "type": "one `rule.promoted` event on the bus, only for staging -> definitive",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.agentic.loop.AgentLoop.stream_chat",
   "callee": "cortex.agentic.notices.PromotionNotices.take",
   "exits": [
    {
     "kind": "returns",
     "type": "one line, oldest first",
     "handled_at": "yielded as the turn's first text delta and carried into every terminal done message"
    },
    {
     "kind": "absent",
     "type": "None when nothing is pending, or when the loop was built without notices",
     "handled_at": "no prefix: the stream is exactly what it was before #173"
    },
    {
     "kind": "raises",
     "type": "nothing: the buffer is a deque operation",
     "handled_at": "nothing to dispatch: the call cannot fail"
    },
    {
     "kind": "effect",
     "type": "the line is consumed: a turn that later fails loses it, which the AC accepts",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.attribution.AttributionRun._apply_proposal",
   "callee": "cortex.rules.service.RuleService.create_dictated",
   "exits": [
    {
     "kind": "returns",
     "type": "the successor, definitive, with its own dictation as one approval use",
     "handled_at": "collected into the rules attribute returns; _reflect swaps it into the list"
    },
    {
     "kind": "absent",
     "type": "RuleReplacementRefusedError (the predecessor is archived) or RuleNotFoundError (it is gone)",
     "handled_at": "one handler here: a warning naming the rule, then the same call without `replaces`, so the dictation still creates its rule"
    },
    {
     "kind": "raises",
     "type": "ValueError on a blank condition or action (before any write), the repository's error",
     "handled_at": "not caught as a refusal: it falls to this method's single outer handler, which logs and drops the proposal"
    },
    {
     "kind": "effect",
     "type": "one rules row, one rule_uses row, and on a replacement one archive write with superseded_by",
     "handled_at": null
    }
   ]
  }
 ]
}
```
