# Cortex — deep dive (2026-11-24)

> A study document, not a source of truth: the specs in `docs/specs/` are
> maintained in place and win on any disagreement. This is the "read the whole
> thing once, in order" map of the system as it stands after rule-system slice B,
> the evidence engine, the eval harness and the safety net landed.
>
> Measured on `develop` = `072e0a7` (merge of PR #179). Source: 21,680 lines of
> Python under `src/cortex/` (+ `src/cortex_protocol/`, `laptop-minion/`); 105
> test modules; `pytest` 1,871 passed, 4 skipped. Every file:line below was read
> at this commit; the numbers are `measured`, the judgements are labelled.

---

## 0. How to study it (reading order that works)

1. `CONTEXT.md` — the vocabulary. Half the bugs in this codebase would be naming
   bugs; the glossary is the defence.
2. `docs/specs/architecture.md` — the map and the invariants (CFG1–6).
3. This document → then the six subsystem specs it points at.
4. `docs/adr/` — 21 decisions, each with the alternatives that were rejected.
   Read them when a design choice looks arbitrary; it usually was not.
5. Then the code, in this order: `main.py` (composition), `agentic/loop.py`,
   `agentic/context_builder.py` + `memory/context_provider.py`,
   `rules/{scoring,service,attribution,queue}.py`, `eval/runner.py`,
   `safety/{journal,backup,shell_guard}.py`.

The rule of thumb for this repo: **the composition root wires; the modules never
construct each other.** If you are reading a module and it builds a repository,
you are reading a bug.

---

## 1. The system on one page

Cortex is a modular monolith: one process, one Postgres, one in-memory event bus,
one LLM provider, plus an optional MQTT path to minions.

```
Layer 6  integration   main.py (composition root) · cortex_protocol · laptop-minion
Layer 5  orchestration api routes · ExecutionModule · InteractionService
Layer 4  agentic       AgentLoop · ContextBuilder · Reasoner · LoopExecutor · PromotionNotices
Layer 3  services      ToolExecutorService · MinionService
Layer 2  modules       sessions · memory · validation · goals · minions ·
                       trace · eval · rules · learning
Layer 1  primitives    events (the River) · llm · tools · db · config · logging
Layer 0  foundation    config · logging · db
```

Two kinds of signal, deliberately distinct:

| | Mechanism | Scope | Example |
|---|---|---|---|
| **LoopEvent** | async generator from `AgentLoop.stream_chat` | one caller, one turn | `text`, `tool_done`, `done` |
| **System event** | `EventBus` publish/subscribe (`events/`) | the whole process | `goal.created`, `concept.validated`, `rule.promoted` |

The loop's progress is *never* on the bus; the domain events are *never* on the
loop stream. `LoopEvent.event_type` is the SSE wire name — no mapping table
(`agentic/events.py`).

One chat turn, end to end (measured path):

1. `POST /chat/stream` → `api/routes/chat.py` → `ExecutionModule.stream_chat`
   (adds fact extraction, trace capture).
2. `AgentLoop.stream_chat` seeds history, persists the user message, then loops:
   `ContextBuilder.build` → `Reasoner.reason[_stream]` → `LoopExecutor` →
   append results → repeat until `RESPOND` or 20 iterations.
3. The route reads the turn's rule snapshot **before** the loop
   (`_snapshot_rules`), streams the events, and on `ResponseDoneEvent` enqueues
   one `queued_feedback` row **after** the answer (`enqueue_turn`).
4. A background drain (every 60 s, `main.py:31`) attributes the queued turns: one
   classifier call per row → `RuleService.record_use` per applied rule → proposal
   / split → `mark_attributed`.
5. A promotion during the sweep publishes `rule.promoted`; `PromotionNotices`
   buffers the line; the next chat turn's first text delta carries it.

---

## 2. Composition root (`src/cortex/main.py`, 554 lines)

`initialize_app()` is the only place that constructs infrastructure. Order
(`measured`, `main.py:120-410`):

1. settings (`get_settings()`), logging;
2. DB pool (`db/pool.py`), then **migrations** (`db/migrations/runner.py`) —
   before anything else touches the schema;
3. event bus (`EventBus.start()`);
4. repositories: sessions, goals, trace, facts, concepts, **evidence store**;
5. **RuleService** with `EventEmitter(bus, "rules")` — the emitter is what lets
   a promotion announce itself;
6. LLM factory: one client for the process (`llm_factory.create()`), plus a
   **separate** `create_for_module("rules")` client with its own circuit breaker
   for the classifier/conflict/split calls;
7. `PostgresFeedbackQueue` + `FeedbackDrain(AttributionRun(FeedbackClassifier,
   RuleService, ConflictCheck, SplitProposer), RuleService)`;
8. memory: `FactStore` (given the evidence store), `ConversationFactExtractor`,
   `ContextProvider` (given the rule service and the validation pipeline);
9. validation: `LogicEngine` → `ConceptValidationService` →
   `ConceptDerivationService` (emits `concept.validated/rejected`);
10. tools: `register_meta_tools(allowed=settings.tools.allow, safety=settings.safety)`;
    `ToolExecutorService` wrapped to the `ToolExecutor` interface;
11. agentic core: `ContextBuilder`, `Reasoner`, `LoopExecutor`,
    `PromotionNotices`, `AgentLoop(notices=…)`;
12. `ExecutionModule` (+ trace recorder), `resume_in_flight()` for goals,
    `InteractionService`;
13. FastAPI app with a state dict; then bus subscriptions; optional MQTT minion
    service;
14. two background tasks: evidence sweep (3600 s) and feedback drain (60 s).

`CortexApp` is a plain container (`main.py:35`), not a DI framework. Every
replacement seam is a constructor argument, which is why tests can build the loop
with fakes without a container.

---

## 3. The River — events (`src/cortex/events/`, 393 lines)

| Piece | Contract |
|---|---|
| `EventBus` (`bus.py`) | asyncio pub/sub; `"*"` wildcard; **per-handler error isolation** (one failing subscriber cannot stop the others); `start()`/`stop()`. |
| `BaseEvent` / `EventMetadata` | `type`, `payload`, `timestamp`, `source_module`, `trace_id`, `session_id`, `salience`, `correlation_id`. |
| `EventEmitter` | the safe publish seam: never raises, tolerates `None` bus. Every module publishes through it. |
| `EventTypes` | closed registry: conversation, sensor (location/payment/activity/calendar/call_log/app_usage), learning, tools/goals, validation, **`rule.promoted`**, lifecycle, `"*"`. |

Why the emitter matters: a lost event costs a notification, never a write. That
is the exact property `#173` relies on (`RuleService.record_use` emits after the
commit; a dead bus costs one line of text).

---

## 4. The agentic loop (`src/cortex/agentic/`, 1,616 lines)

### Components

| File | Contract |
|---|---|
| `loop.py` | `stream_chat` (the one implementation), `run_chat` (drain), `run_goal`; caps 20 / 100 iterations. |
| `context_builder.py` | `build(session_id, user_message, mode, *, goal_id, fact_types) -> Context`: last `max_messages - 1` (20) messages, memory bundle (`max_facts=10`), tool schemas, personality, ambient, goal context. |
| `reasoner.py` | `reason` / `reason_stream` → typed `Decision` (`RESPOND` \| `EXECUTE_TOOLS`); builds the system prompt and renders the rule section (`reasoner.py:182`). Malformed output fails loudly (ADR-0005/0018). |
| `executor.py` | `execute_single`, `execute_tools(parallel, max_parallel=5)`; tool errors become observations, not crashes. |
| `models.py` | `Decision`, `Mode`, `Context`, `MemoryContext` (the 4-dimension bundle), `Goal*`, `MaxIterationsError`. |
| `events.py` | the 7 `LoopEvent` types + `to_dict()`; `ResponseDoneEvent` carries message, tools, iterations, usage, latency. |
| `notices.py` | `PromotionNotices` — bounded FIFO (20) fed by `rule.promoted`; `take()` once per turn. |

### Behaviour worth knowing

- **Reasoning** is normalised by the provider into an inline `<think>…</think>`
  block (ADR-0018); `Decision.reasoning` is a placeholder. The classifier reads
  the `<think>` block from the assistant turn, never the `thinking` event.
- `ask_user` is **not** a decision type: the model calls the tool, the loop
  yields `AskUserEvent` and then `done` (ADR-0018).
- Response deltas can arrive **before** the iteration's `thinking` event;
  consumers must tolerate both orders.
- On error: one `error` event, then the original exception is re-raised out of
  the generator. `run_chat` does not swallow it; the streaming route returns
  silently for expected conditions (`max_iterations`).
- The promotion line rides the first `text` delta, appears in the `done`
  message, and is **not** written to session history — so the classifier (which
  reads `queued_feedback.assistant_turn`, built from the model's text) never
  sees it.

---

## 5. Memory (`src/cortex/memory/`, 2,537 lines)

Three separate things share the package name:

### 5.1 Facts and the evidence engine

- `FactStore` (`fact_store.py`) owns CRUD + dedup by `symbolic_repr`; every
  observation goes through `EvidenceStore.record`, which owns
  `facts.confidence` (ADR-0013). **Confidence is never written by hand.**
- `EvidenceStore` has two implementations of one contract: the synchronous
  in-memory one (the executable E1 spec) and `PostgresEvidenceStore`.
- Math (`evidence_store.py`, `update_rule`):

  `logit(p') = logit(p) + (2·strength − 1)·ln(LR_base(source_type))`

  with `LR` = 20 (user_confirm), 10 (sensor), 3 (llm); prior 0.5; dedup window
  1 h; validity windows 1 day / 182 days / 1825 days / none by mutability; hourly
  sweep to keep stored confidence equal to the posterior as evidence expires.
- An evidence is **active** iff inside the window **and** its `value_hash`
  matches the fact's current value — that is why a lie followed by a correction
  does not need deletion.
- Static facts are pinned at 1.0, record no evidence, are never retracted.

### 5.2 The context bundle (the seam every consumer uses)

`ContextProvider.get_memory_context()` returns `MemoryContext` with four
dimensions — **facts, personality, ambient, rules** — each in its own
`try/except` and each failure appended to `degraded_dimensions`
(`context_provider.py:52-104`). A broken rule query degrades the rules dimension
and leaves the rest of the context intact. Rules are fetched by
`RuleService.list_active` (`get_rules_context`), which *sweeps* on read.

Retrieval: semantic when the LLM client can embed (ADR-0008, `vector(1536)` on
`facts`, migration 009, ivfflat cosine index with `lists = 100`), lexical fallback internally; `min_relevant_confidence`
0.4; at most `max_facts` (10) per turn.

### 5.3 Ingestion and concepts

- `FactExtractor` — deterministic mapping of sensory events (location, payment,
  activity, calendar, call_log, app_usage) to facts, plus an LLM path.
- `ConversationFactExtractor` — user message → LLM extraction → `FactStore`,
  best-effort, before the loop runs (`ExecutionModule._extract_user_facts`).
- Validation: `LogicEngine` (strict Datalog parser + PyDatalog) →
  `ConceptValidationService` → `ConceptDerivationService` (LLM revision loop,
  emits `concept.validated`/`concept.rejected`); only validated concepts are
  stored (`ContextProvider.store_concept`).

---

## 6. Rules (`src/cortex/rules/`, 1,776 lines) — the newest subsystem

Spec: `docs/specs/rule-system.md` (680 lines). `CONTEXT.md` § Rules for
vocabulary. Two slices: A = mechanics (repository, service, score, render);
B = the feedback loop (#168–#175, merged in #176–#179).

### 6.1 The score (`scoring.py`, `constants.py`) — pure, no numbers inside

```
logit(p') = logit(prior) + Σ direction(use) · ln(LR(outcome))
```

| Constant | Value |
|---|---|
| prior (correction-born) | 0.5 |
| dictated rule | created as `definitive` with one `approval` use → `sigmoid(ln 20) ≈ 0.952` |
| `LR(approval)` / `LR(correction)` | 20 / 20 |
| `LR(implicit)` | 3 |
| `LR(neutral)` / `LR(scope_correction)` | 1 |
| validity window | 183 days |
| promote / demote / archive | 0.9 / 0.7 / 0.3 |
| promotion minimums | 3 positive uses, 2 distinct sessions |
| context budget | 40 rules |

Only **active** uses count; the stored confidence is always the recomputed
posterior, never an accumulated counter. `next_state` is a pure function of
(current state, confidence, uses, now).

### 6.2 Service (`service.py`) — the ordering of the writes

`create_dictated(session_id, spec, *, replaces=None)`, `create_from_correction`,
`record_use`, `list_active` (sweeps, then orders by recomputed confidence and
caps at 40), `archive`, `split`, `merge`, `get`, `list_uses`.

- A replacement validates the predecessor **before the first write**
  (`RuleNotFoundError` / `RuleReplacementRefusedError`), creates the successor
  from its own dictation alone, archives the predecessor with
  `superseded_by`, and **keeps the predecessor's uses** (#174).
- `split` validates two children, one assignment per use, archives the parent,
  keys the children's state to the assignment (no uses → both staging); not
  transactional in v1.
- The promotion edge: `record_use` calls `_announce_promotion` after the refresh
  commits, publishing `rule.promoted` through the optional emitter, and the line
  is built by `render.promotion_line` (fixed copy, not the model's).

### 6.3 Classifier (`feedback.py`)

One LLM call per attributed turn with the rules that turn had in context, the
assistant turn (reasoning included), and the user's reply. Returns per rule
"applied?" + one category (`approval`, `implicit`, `correction`,
`scope_correction`, `neutral`), plus at most one proposal (`RuleSpec`, `origin`,
`replaces`). The parse is tolerant and drops evidence it cannot trust: an
invented rule id, an unknown category, a blank condition/action, an unknown
`replaces`. Malformed JSON raises — the caller queues instead of losing the
turn. **A category, never a number**: the weight comes from the table.

### 6.4 Queue + drain (`queue.py`)

Migration 011: `queued_feedback` (session, `rule_ids[]`, assistant turn, reply
with `CHECK length > 0`, status, timestamps), `rule_uses.feedback_id`, and the
partial unique index `(feedback_id, rule_id)`. The unique index is the
idempotency mechanism: a retry fills the gaps, cannot double-score.

- Enqueue happens at the loop's caller, after the answer; `enqueue_turn` never
  raises and skips a turn with no rules or a blank reply.
- `FeedbackDrain.drain(limit=10)`: pending rows → rules by id → `attribute` →
  `mark_attributed` when the run returned a list (even an empty one: nothing
  more to say); failing rows stay pending; `drop_stale` past the validity window.
- Runs every 60 s from the composition root, never inside a request.

### 6.5 Attribution (`attribution.py`)

Inputs: the turn's rules, session, assistant turn, reply, situation,
`feedback_id`. Behaviour on failure: **write less, never more** — a classifier
error writes nothing and returns `None` (row retried); a failed `record_use` is
logged and the others continue; a proposal that contradicts the active set, or
whose write fails, costs the proposal, not the uses; a refused replacement
retries once without `replaces` (a duplicate beats a lost dictation). The
returned list is the post-mutation state (`_reflect`), so callers see the
archived predecessor and the split parent as they are now.

### 6.6 Prompt side (`render.py`, `reasoner.py:182`)

`render_rules` renders `- condition → action`, adds ` (because rationale)` and,
for **staging** rules only, `[provisional]` plus one instruction line. Archived
rules are dropped. `list_active` gives the 40 highest-confidence rules; rules
beyond the budget simply do not enter (universal-condition rules are not
modelled — documented gap).

### 6.7 Manual surface

`cortex rules:list`, `rules:show <id>` (state, confidence, uses, both directions
of the history edge: `superseded by:` and `replaced:`), `rules:archive`.
Archiving is explicit and prevails over the classifier. There is no way to edit
confidence or state directly.

### 6.8 Invariants that actually hold (spec table + CI)

Non-contradiction of active rules is **not** a stored constraint: it is checked
by an LLM pass at creation (`ConflictCheck`), so it is measured by eval, not by
a test that can fail mechanically. The mechanically pinned ones: confidence equals
the posterior; confidence in [0,1]; expired uses contribute nothing; silence
never moves the score; a rule scores only on uses it applied; archived rules are
never applied; one turn scores a rule at most once (unique index); a blank reply
is never enqueued; an unusable classifier answer writes nothing; a replacement
moves no evidence; one promotion → one line; eval never learns; `rules`,
`rule_uses`, `queued_feedback` are written only by `cortex.rules` (import-linter
contracts on `rules.repository` and `rules.scoring`, run in CI job
`Boundaries`).

---

## 7. Evaluation (`src/cortex/eval/`, 2,547 lines)

Spec: `docs/specs/eval-system.md`. pytest-native, no external framework
(ADR-0014).

| Piece | Role |
|---|---|
| `fixtures.py` | YAML suite schema: suite header, tasks (`turns`, `goal`, `sandbox`, `description`, and since #175 `rules: [{condition, action}]`), manifest loader. |
| `runner.py` | builds the **real** `AgentLoop` inside a `TaskSandbox`, one loop per task; the only injected dependency is the LLM client. `_FixtureContextProvider` serves the task's rules as a `MemoryContext` (definitive, confidence 1.0, fixture order = prompt order; no store, no cap). |
| `sandbox.py` | `mkdtemp` per task; meta tools wrapped so file ops resolve inside the sandbox. |
| `grader.py` | the only pass/fail oracle: deterministic goal state (files `equals`/`contains`/`json_equals`, `absent`, answer matching, refusal check). `GRADING_SCHEMA_VERSION = 2`. |
| `judge.py` | Trajectory Judge, **failed tasks only**, never pass/fail; G-Eval rubric, order-swap position-bias guard, partial credit, human audit. |
| `metrics.py` | iterations, tools, usage, latency, cost; `pass^k`. |
| `baseline.py` / `gate.py` | versioned per-suite baseline JSON; gross-regression gate (2σ), pure function + tiny CLI. |

Suites: **E1** evidence invariants (deterministic PR gate), **E2** loop (30
tasks, nightly), **E6** refusal (13 tasks, balanced must-refuse/must-comply,
nightly). The capability suite was removed (tombstone in `CONTEXT.md`).
Manifests pin prompt/model/grading/rubric versions; baselines live in the
Actions cache `eval-baseline-v1`.

**#175's contract**, worth remembering: an eval run learns nothing. The path
constructs no rule repository, service or queue; `tests/eval/test_rule_fixtures.py`
spies `RuleService.record_use`/`create_*` **and** `queue.enqueue_turn`, runs a
rules-carrying suite green, and asserts the fixture rules are unchanged.

---

## 8. Safety net (`src/cortex/safety/`, 1,004 lines)

Born from the 2026-10-28 incident (`docker compose down -v` destroyed the dev
DB). ADR-0021, spec `safety-net.md`; slices #146/#147/#148.

| Slice | Mechanism |
|---|---|
| DB backup (#146) | `cortex backup:create` → `pg_dump --format=custom`, timestamped, atomic rename, `0600`, retention 14 (never delete the newest); host `pg_dump` preferred, compose exec fallback. `backup:restore` confirms, refuses while the DB is in use unless `--force`. |
| Pre-migration (#146) | `run_migrations` dumps when migrations are pending; **fail closed** by default (`SAFETY_REQUIRE_BACKUP_BEFORE_MIGRATION=true`). |
| Snapshots + undo (#147) | before `file_write` **overwrites** an existing file: `snapshots/<id>/original` + one journal line; failed write → restore + abort marker; `cortex undo last|<id>` restores and appends an undo marker. Journal is append-only; status is derived from markers. Fail closed on snapshot failure. |
| Shell guard (#148) | table-driven deny-list in `shell_guard.py` (rm -rf, compose down -v, volume rm/prune, git reset --hard/clean -fdx, DROP/TRUNCATE, mkfs, dd of=, global uninstalls, pkill/kill -9). A match is refused, journaled, never executed; `SAFETY_SHELL_GUARD=false` is an explicit journaled bypass. Fails **open** on unknown commands: mitigation, not sandbox. |

The store lives at `SAFETY_STORE_DIR` (`~/.local/share/cortex`), outside the repo
and outside every Docker volume — that is invariant 1. Known residual: container
runs as root (#120), so `chown` can fail on rootless/NFS; file undo needs one
filesystem namespace (host-local Cortex).

---

## 9. Trace & audit (`src/cortex/trace/`, 736 lines)

Opt-in per session (`sessions.trace_enabled`). `ExecutionModule.stream_chat`
wraps the loop stream with `TraceRecorder.capture` when the session opted in and
a recorder was injected. Per event: `to_dict()`, pseudonymize each PII-bearing
string via the local `rizzo-pii` sidecar (`include_mapping=false`), persist the
**copy** to `loop_events`, yield the original untouched. Capture is fail-closed
toward the caller: a sidecar or DB failure never alters the user's stream.

`trace/audit.py` reconstructs typed events from seq order, describes the session
to the judge using the first user turn (pseudonymized), re-pseudonymizes stored
raw error text. Judge = diagnosis only. `traces:cleanup` deletes older than
`trace.retention_days` (default 30). Scope boundary: pseudonymization at
persistence only — the **PII Gateway** (anonymize model calls) does not exist
(needs a pre/post hook, issue #106).

---

## 10. LLM layer (`src/cortex/llm/`, 1,084 lines)

- `LLMClient` (ABC): `chat`, `chat_stream`, `embed`, `translate_tools`,
  `translate_tool_call`, `get_provider_name`, `from_settings`.
- One provider in `PROVIDER_MAP` today: `openai` (`providers/openai.py`),
  OpenAI-compatible — DeepSeek et al. work through `base_url`.
- **Reasoning normalisation** (ADR-0018): out-of-band `reasoning_content` /
  `reasoning` becomes an inline `<think>…</think>` block, on `chat_stream` and
  (since #168) on `chat`; this is what the rule classifier reads.
- `LLMClientFactory`: `create()` (plain) and `create_for_module(name)`
  (wraps in `CircuitBreakerLLMClient` with its own breaker). The rules module is
  the first caller of `create_for_module` — a classifier outage opens its own
  circuit and leaves chat alone.
- `CircuitBreaker`: CLOSED → OPEN → HALF_OPEN; `CircuitOpenError` carries
  `opened_at`/`retry_after`; thresholds from `LLMSettings`.
- `GenerationConfig` (temperature, max_tokens, top_p, seed, response_format) is
  the deterministic-call seam the classifier/conflict/split use.

---

## 11. Configuration (`src/cortex/config/`, 672 lines)

`Settings` = 10 slices: `database`, `llm`, `mqtt`, `app`, `trace`, `logging`,
`learning`, `tools`, `safety`, plus `version`.

Precedence (ADR-0019, `CortexSettings.settings_customise_sources`):
**env > constructor/YAML > .env > secrets/defaults**, applied identically at the
root and inside nested slices.

Six invariants, each with a test that can fail (CFG1–6 in `architecture.md`):
env beats YAML; YAML beats `.env`/default; a constructor kwarg beats `.env` and
loses to env; test runs scrub ambient settings env; an unresolved `${VAR}` drops
the key; a missing API key fails loudly naming the field.

`TOOLS_ALLOW` is a deny-by-default CSV/list read with `NoDecode`; unknown names
raise at startup (`register_meta_tools`), not silently.

---

## 12. Persistence (`migrations/`, 11 files)

| Migration | Contents |
|---|---|
| 001 | `schema_migrations`, `sessions`, `messages`, `facts`, `concepts` (+ indexes) |
| 002/004 | sessions/messages rework, `last_activity_at` |
| 003 | facts/concepts |
| 005 | `api_keys` (hashed), `minions`, `goals`, `goal_steps` |
| 006 | `messages.tool_call_id` |
| 007 | `loop_events` (traces), `sessions.trace_enabled` |
| 008 | `rules`, `rule_uses` |
| 009 | `facts.embedding vector(1536)` + HNSW + null-backfill index |
| 010 | `evidence` |
| 011 | `queued_feedback`, `rule_uses.feedback_id`, partial unique index |

Ten tables hold state: sessions/messages, facts/concepts/evidence,
goals/goal_steps, minions, api_keys, loop_events, rules/rule_uses,
queued_feedback. Rules own their own evidence table (`rule_uses`), by design —
they share the mathematics with facts, not the store.

Every migration runs in its own transaction after the pre-migration dump gate.

---

## 13. API and CLI surfaces

FastAPI (`api/routes/`): `chat` (`POST /chat`, `POST /chat/stream` SSE),
`sessions`, `goals`, `minions` (admin), `health`, `admin_auth` (token CRUD).
Auth: hashed API keys in `api_keys`, `get_api_key` dependency, cached token
lookup. SSE frames use `event_type` verbatim; the `done` frame renames
`message`→`final_message`, `tools_used`→`tool_calls`; a missing session is an
HTTP 404 before the stream starts.

CLI (`src/cortex/cli.py`): `token:create`, `traces:cleanup`, `rules:list`,
`rules:show`, `rules:archive`, `memory:backfill-embeddings`, `backup:create`,
`backup:restore`, `undo`, `version`. Same `SAFETY_STORE_DIR` as the container.

Deployment: `docker-compose.yml` with `postgres` (pgvector:pg16),
`mosquitto`, one-shot `safety-store`, `cortex`, opt-in `rizzo-pii`. Host ports
5433 for Postgres; port 8000 for the app; container runs as root for now
(#120).

---

## 14. Background work and failure isolation

| Task | Period | Where | Failure behaviour |
|---|---|---|---|
| Evidence sweep | 3600 s | `main._evidence_sweep_loop` | logged, retried next tick |
| Feedback drain | 60 s | `main._feedback_drain_loop` | logged, pending rows retried |
| Goal runs | per goal | `ExecutionModule._spawn_goal_task` | `goal.failed` event, status persisted |
| MQTT client | continuous | `minions/mqtt_client.py` | reconnect loop |
| Trace capture | per turn | consumer of the stream | fail-closed toward the caller |

The repeated pattern across the system: **the read path isolates per dimension,
the write path degrades toward doing less, and the event bus isolates per
handler.** Memory dimensions (`degraded_dimensions`), rules (a failed classifier
writes nothing), traces (a failed sidecar never breaks the reply), the bus
(a failing subscriber cannot stop the others) all implement the same rule.

---

## 15. Gates and invariants in CI

`.github/workflows/ci.yml`: Tests (pytest), Lint (ruff), Typecheck (mypy strict),
Coverage, Boundaries (`lint-imports`, 2 contracts), Docker build.
`.github/workflows/eval.yml`: E1 deterministic PR gate (path-filtered), nightly
E2/E6 + baseline gate.

Import contract today: `cortex.rules.repository` and `cortex.rules.scoring` are
private to `cortex.rules` (the composition root and CLI may reach the repository
because they build it). Everything else is **not frozen**: the whole-map
contracts are red by design (`memory ↔ agentic`, `config → llm → tools →
config`, four modules reaching up), which is issue #167 (MODULARIZE), not a
passing check. This is the single most important thing to know when reading the
layer diagram: it is a target, not a verified property.

Tests: 105 modules in `tests/` (unit, memory, config, learning, validation,
safety, eval, e2e, integration) plus eval suites in `tests/eval/fixtures/`.
1,871 tests, ~23 s. The rule of the house: a law without a test that can fail is
a comment — and the mutation prover is how coverage claims are proven.

---

## 16. Where the system is thin (judgement, labelled)

- **Cycles and upward edges** (measured by import-linter today): `memory ↔
  agentic`, `config → llm → tools → config`; `memory`, `goals`, `trace`, `eval`
  reach up the layers. `deduced`: the layer diagram is aspirational until #167.
- **Split/merge are not transactional** (measured, spec's own limit): a failure
  mid-way leaves children created and the parent active; the operator re-runs.
- **Replacement race** (measured, `shortcut:` comment): two concurrent
  replacements can both pass the read-then-write archived check.
- **Conflict check is LLM judgment** (measured): "active rules never contradict"
  is not mechanically enforced; only eval can measure it.
- **Rules beyond 40** (measured): no retrieval behind the budget, no
  universal-condition field; documented open question.
- **Minion path** (measured from the architecture note): MQTT → `MinionService`
  is not wired end to end; `laptop-minion` and protocol exist.
- **PII at the model boundary** (measured): absent; traces are pseudonymized at
  persistence only.
- **Container root** (measured, docs): #120's sidecar is the real trust boundary;
  the shell guard is a mitigation and fails open on unknown commands.
- **`LearningModule`** (measured, P4 of the rule spec): an ESN with no LLM
  client, not mounted in the composition root — configured, not running.

---

## 17. Study checklist (what to read next, in this order)

1. `src/cortex/main.py:120-410` — the wiring, once, slowly. Every arrow in the
   architecture diagram is built here.
2. `src/cortex/agentic/loop.py` — the only control flow that matters; then
   `context_builder.py` and `reasoner.py`.
3. `src/cortex/memory/context_provider.py:52-104` — the bundle and its
   degradation; `evidence_store.py` `update_rule` for the math.
4. `src/cortex/rules/scoring.py` + `constants.py` (5 minutes), then
   `service.py` (`create_dictated`, `record_use`, `list_active`), then
   `attribution.py` and `queue.py`.
5. `migrations/008` and `011` — the rule tables and the idempotency index.
6. `src/cortex/api/routes/chat.py` — the snapshot-before / enqueue-after seam.
7. `src/cortex/eval/runner.py` — how the harness drives the real loop with fakes.
8. `src/cortex/safety/journal.py` — the append-only journal and the undo
   markers; `backup.py` for the pre-migration gate.
9. `docs/adr/0020` (rules) and `0013` (evidence) — the two score designs and
   their rejected alternatives; `0021` for the incident that created the net.
