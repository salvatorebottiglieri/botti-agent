# Botti-Agent

Personal AI agent system. Cortex provides the core LLM orchestration layer, with modules (Execution, Memory, Learning) consuming LLM services through a factory and client abstraction.

## Language

**LLMClient**:
Provider-agnostic abstract interface for LLM interactions. Each provider (OpenAI, Anthropic) implements this interface.
_Avoid_: LLM, model, provider client

**CircuitBreaker**:
State machine (CLOSED → OPEN → HALF_OPEN → CLOSED) that wraps an LLMClient to fast-fail when failures exceed a threshold within a time window. Prevents thundering-herd retry storms against a rate-limited provider.
_Avoid_: Retry handler, fallback, breaker

**CircuitBreakerLLMClient**:
Wrapper that implements the same interface as LLMClient and delegates calls through a CircuitBreaker. Transparent to callers — they receive normal return values when closed, `CircuitOpenError` when open.

**CircuitOpenError**:
Exception raised when a call is attempted while the circuit is OPEN. Carries `opened_at` and `retry_after` for recovery-aware retry logic.

**CircuitState**:
Enum: CLOSED, OPEN, HALF_OPEN. Represents the current state of a circuit breaker instance.

**LLMFactory**:
Creates LLMClient instances. Each consumer module (Execution, Memory, Learning) gets its own CircuitBreakerLLMClient with an independent CircuitBreaker, so failures in one module don't affect others.
_Avoid_: Factory, provider factory

## Agentic Loop

**LoopEvent**:
A progress signal the AgentLoop emits for its caller (SSE, CLI) as it runs: thinking, text deltas, tool start/result, done, error. Caller-scoped — never published on the event bus.
_Avoid_: Stream event, loop bus event

**TextDeltaEvent**:
A unit of the loop's response text delivered to the caller. One event per response today (carrying the full text); multiple deltas per response are possible only if the reasoner ever streams tokens. Consumers append deltas and must never assume chunk size.
_Avoid_: Token event, streaming chunk

**ResponseDoneEvent**:
The loop's final-response signal, emitted once per response after the last TextDeltaEvent. Carries the full response text, the tool names used, and the iteration count — the metadata a non-streaming consumer needs for its final result. It also carries the accumulated token usage (`usage`) and the response latency in milliseconds (`latency_ms`).
_Avoid_: Done event, completion event, final event

**Drain wrapper**:
The consumer that takes the loop's streaming progress and reduces it to a single final response, discarding intermediate signals (thinking, tool activity). Uses the stream when progress matters; uses the drain when only the result matters. Loop errors surface unchanged — the drain never swallows or rewraps them.
_Avoid_: Convenience wrapper, non-streaming call, response collector

**SSE adapter**:
The consumer that serializes LoopEvents to SSE frames for the streaming chat endpoint (`/chat/stream`). The wire name of each frame IS the event's `event_type` — one vocabulary, no mapping table — and frames are explicit and minimal: the `done` frame renames `message`→`final_message` and `tools_used`→`tool_calls`. A loop error ends the stream with an `error` frame carrying the error text and code; session lookup happens before the stream starts, so a missing session is an HTTP 404, not an in-stream error.
_Avoid_: Stream adapter, SSE endpoint, chat stream

**System Event**:
An event published on the Cortex event bus for any module to consume (e.g. goal.created, concept.rejected). Distinct from LoopEvent: system events are system-wide domain information; LoopEvents are per-caller progress.
_Avoid_: Domain event, bus event
## Memory

**Fact**:
A unit of knowledge Cortex stores about the user and their world: a symbolic representation (`symbolic_repr`), a natural-language form, a confidence, a mutability, and a payload. Facts are observed directly, extracted from conversations, or derived.
_Avoid_: memory item, knowledge unit, note

**Confidence**:
The posterior probability that a Fact is true, in [0, 1]. Computed by a Bayesian odds update over accumulated Evidence; pinned at 1.0 for Static Facts. Serves as the base of relevance ranking and the `min_confidence` retrieval threshold.
_Avoid_: certainty, trust score, strength

**Evidence**:
An observation that updates a Fact's confidence — a sensory event, a user confirmation, or an LLM extraction touching the same symbolic representation. Carries a source type (sensor, user_confirm, llm), a strength (the confidence of the ingested fact), and a timestamp. Deduplicated per (source, source_id, fact) within a time window.
_Avoid_: proof, confirmation, data point, supporting observation

**EvidenceStore**:
The module that records Evidence and recomputes Fact confidence through the Bayesian update. Owns the deduplication window and the likelihood-ratio constants.
_Avoid_: confidence engine, scorer, confidence store

**Likelihood ratio (LR)**:
The per-source constant encoding how strongly a full-strength Evidence of that source moves a Fact's confidence — the calibration point of the Bayesian update.
_Avoid_: weight, importance, source reliability

## Rules

Term definitions for the learned-rule system. The decision is in
`docs/adr/0020-learned-rules.md`; the mechanics in
`docs/specs/rule-system.md`.

**Rule**:
A learned prescription Cortex applies when a condition holds — condition →
action → rationale. Distinct from a Fact: a Fact is scored on whether it is
true, a Rule on what happened each time it was followed. Rules start in staging
until enough positive evidence accumulates, are split when a correction shows
them too broad, and are merged when the set grows.
_Avoid_: instruction, policy, preference, guideline

**Rule confidence**:
The posterior probability that following a Rule goes well, in [0, 1]. Computed
by the same logit-space odds update as Fact Confidence, but over the outcomes of
the Rule's uses instead of observations of truth.
_Avoid_: score, rating, truth probability

**Staging**:
The state of a Rule that is still being tried. A staged Rule is in the context
like any other Rule and declares itself to the user in one line whenever it is
applied. Promotion to definitive requires confidence ≥ 0.9, at least 3 positive
uses and at least 2 distinct sessions.
_Avoid_: probation, trial, candidate, pending

**Split**:
The replacement of one Rule by two complementary Rules with disjoint conditions,
triggered by a correction showing the Rule is too broad. The Rule preserving
the parent's behaviour inherits its state and the uses that fall in its
condition; the new one starts in staging and takes the uses in its own.
_Avoid_: refinement, exception, derivation, parent/child rules

**Merge**:
The fusion of several Rules into one covering the union of their conditions.
Proposed by Cortex, always approved by the user. The fused Rule takes the union
of the evidence and is definitive only if all its parents were.
_Avoid_: consolidation, generalization, grouping

**Use attribution**:
The decision, taken after the turn by the feedback classifier, of which Rules
were actually applied. A Rule present in context but not applied takes no
score, and every use records the situation it was applied to, which is what
lets a Split redistribute history instead of copying it.
_Avoid_: declared use, cited rules, relevance

**Feedback classifier**:
The asynchronous LLM component that attributes uses and categorises the user's
next message per Rule — approval, implicit acceptance, correction of the
decision, scope correction, or neutral. It reads the turn's reasoning as well
as its text, where the model produced any. It returns a category, never a
number: the weight comes from the table, because a model's confidence in
itself is not calibrated.
_Avoid_: grader, evaluator, scorer

**Reasoning**:
The model's own account of why it acted, produced out of band by reasoning
models and normalised by the provider into an inline `<think>...</think>` block
(ADR-0018). Distinct from the loop's `thinking` event, which carries a
placeholder and no model thought.
_Avoid_: thinking event, chain of thought, scratchpad

**Rule declaration**:
The one-line notice the **model** writes in its response when it applies a
staged Rule, so the user can confirm or correct it. It follows a prompt
instruction and is best-effort: it is written before the turn ends, while use
attribution is decided after. Definitive Rules are silent — only the use
attribution records them.
_Avoid_: announcement, notice, trace

## Evaluation

Term definitions for the eval system. The suites, harness, grading, judge and
CI gate are specified in [`docs/specs/eval-system.md`](docs/specs/eval-system.md);
decisions in ADR-0014/0015/0016.

**Eval Task**:
A self-contained evaluation case with an annotated goal state (filesystem, database, or exact answer). Scripted user turns drive the loop; the graded outcome is the final state, not the transcript.
_Avoid_: test case, scenario, golden example

**pass^k**:
The probability that all k trials of a task succeed. The consistency metric for agent behavior — more meaningful than average success for a user-relying agent.
_Avoid_: accuracy, success rate

**Trajectory Judge**:
An LLM that evaluates the problem-solving path of a failed Eval Task, producing partial credit and a diagnosis. Never the pass/fail oracle — that role belongs to the goal state.
_Avoid_: LLM grader, evaluator model

**Partial Credit**:
How far an agent progressed toward the goal state before failing, as scored by the Trajectory Judge. Captures the continuum of success ("identified the problem but botched the refund").
_Avoid_: score, quality grade

**Capability Eval** (removed):
Measured raw model behaviour on single-message questions. Removed — loop and
refusal cover the surfaces that matter for a personal agent. Kept as a tombstone
so future readers know it existed and why it's gone.
_Avoid_: quality eval


**Regression Eval**:
A suite asking "does Cortex still do everything it used to?" — should sit near 100% pass; a drop is a bug signal.
_Avoid_: smoke test

**Eval Baseline**:
The recorded metrics of a suite on a known-good run; nightly changes are compared against it, and gates trigger only on gross regressions in v1.
_Avoid_: reference, benchmark score

**Golden Set**:
The versioned ground-truth data in git (task fixtures, goal states, audit labels). Kept private — never fed to prompts, training, or the learning loop.
_Avoid_: dataset, eval data

## Trace & Audit

Term definitions for opt-in runtime traces. The capture/audit pipeline is
specified in [`docs/specs/trace-system.md`](docs/specs/trace-system.md);
decision in ADR-0017.

**Trace**:
The persisted, opt-in record of one session's agent-loop event stream (thinking, text, tool start/result, done, error + usage/latency), stored pseudonymized so it carries no real PII. Distinct from the conversation (the `messages` table, which feeds the context builder) and from a transcript (the in-memory `Sequence[LoopEvent]` the judge consumes).
_Avoid_: log, telemetry, conversation history

**Trace Recorder**:
The consumer of the loop's event stream that persists each event (pseudonymized) for a session, wired only when the session has tracing enabled. A consumer alongside the SSE adapter and the drain — never a change inside the loop.
_Avoid_: logger, exporter, telemetry sink

**PII Gateway**:
A separate feature that pseudonymizes the context before any API model call and restores the real values locally from the response, so frontier models never see real PII. Depends on a pre/post-hook seam Cortex does not yet have.
_Avoid_: redaction layer, scrubber

**Alias dictionary**:
The local placeholder→real-value mapping produced by the pseudonymizer (rizzo-pii). Stays on the user's machine, never in the Cortex DB, and is never sent to any API model.
_Avoid_: keyring, mapping table, vault

**Pre/post hook**:
A seam that wraps LLM calls — a pre-hook (transform outbound context) and a post-hook (transform inbound response, resolve aliases in tool arguments). Required by the PII Gateway; not present in Cortex today.
_Avoid_: middleware, interceptor, plugin
