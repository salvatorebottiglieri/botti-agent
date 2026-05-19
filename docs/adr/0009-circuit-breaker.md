# Circuit breaker on LLM calls

The Architecture doc specified a circuit breaker pattern (CLOSED → OPEN → HALF_OPEN →
CLOSED) with thresholds: 5 failures in 60s opens the circuit, 30s open duration, 3
half-open successes to close. This was never implemented — every LLM-owning module
(Execution, Memory, Learning) retried independently with no shared failure awareness,
risking thundering-herd retry storms against a rate-limited provider.

We added `CircuitBreaker` as a standalone module in `src/cortex/llm/`. It wraps
`LLMClient.chat()` via the factory, so all modules get wrapped clients with no module
code changes. Callers see normal `ChatResult` return values; the breaker intercepts
exceptions, tracks failure windows, and fast-fails with `CircuitOpenError` when open.
