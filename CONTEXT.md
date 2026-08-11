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

**System Event**:
An event published on the Cortex event bus for any module to consume (e.g. goal.created, concept.rejected). Distinct from LoopEvent: system events are system-wide domain information; LoopEvents are per-caller progress.
_Avoid_: Domain event, bus event
