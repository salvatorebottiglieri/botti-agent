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

## Evaluation

The eval system has two suites: **loop** (the real agent loop under stress: tool
selection, arguments, multi-turn, and safety negatives) and **refusal** (the
refusal policy: must-refuse harmful / PII / policy prompts paired with
must-comply benign prompts as an over-refusal guard). Both suites are balanced
golden sets, versioned per suite (1.x), with manifests pinning prompt/model/
grading/rubric versions so baseline drift is attributable. A Trajectory Judge
(LLM distinct from the generator, ADR-0015) runs only on failed tasks for
partial credit and diagnosis; the deterministic goal state is the only pass/fail
oracle. Cost and latency are tracked per task; judge cost is tracked separately.
Grading is v2 (ADR-0016): `equals` tolerates trailing whitespace, `json_equals`
compares JSON semantically, refusal uses keyword + forbidden-pattern semantic
checks, and the comply `answer` list is matched as a substring any-of after
normalization so natural-language wrappers ("X is Y", "X stands for Y") pass
without enumerating every phrasing. The capability suite that previously
measured raw model behaviour on single-message questions was removed — it
tested a surface the personal-agent use case doesn't need (we don't develop
models; loop + refusal cover the surfaces that matter for a personal agent).
Avoid_: benchmark, test battery

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
Capability measured raw model behaviour on single-message questions — it tested
a surface the personal-agent use case doesn't need. Loop + refusal cover the
surfaces that matter for a personal agent (real agent loop under stress, and
the refusal policy). Kept here as a tombstone so future readers know it
existed and why it's gone.
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
