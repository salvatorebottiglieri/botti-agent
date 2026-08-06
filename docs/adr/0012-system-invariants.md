# ADR-0012: System Invariants — CircuitBreaker and SessionPolicy

Status: accepted (2026-08-06)

Per the agentic workflow (WORKFLOW.md phase 3), domain laws are recorded in
the campaign format — law → negation → where verified — and each law MUST
have a test that can fail (anti-vacuity: an invariant without a test that can
fail is an opinion). This ADR is the canonical home for the invariants of the
modules written so far.

---

## CircuitBreaker (`src/cortex/llm/circuit_breaker.py`)

### CB1 — OPEN fast-fails without executing the underlying call

- **Law**: while the circuit is OPEN (before `recovery_timeout` elapses),
  `call()` raises `CircuitOpenError` and the wrapped coroutine is NEVER
  awaited.
- **Negation**: a call while OPEN awaits the provider (thundering herd).
- **Where verified**: `tests/unit/test_circuit_breaker.py::
  TestCircuitBreakerClosedToOpen::test_open_never_executes_underlying_coro`
  (added 2026-08-06; was unpinned — the old test only asserted the error).

### CB2 — OPEN transitions to HALF_OPEN exactly after recovery_timeout

- **Law**: before `recovery_timeout`, calls stay OPEN; on the first call at/after
  the timeout, the circuit moves to HALF_OPEN with the success counter reset
  to zero.
- **Negation**: the circuit recovers early, or never recovers.
- **Where verified**: `test_transitions_to_half_open_after_timeout`,
  `test_stays_open_before_timeout`.

### CB3 — one failure in HALF_OPEN reopens the circuit with a fresh timer

- **Law**: any failure in HALF_OPEN trips the circuit back to OPEN and
  restarts `opened_at`/`retry_after`.
- **Negation**: a HALF_OPEN failure keeps the circuit half-open.
- **Where verified**: `test_half_open_failure_opens_again`,
  `test_retry_after_updates_on_reopen`.

### CB4 — CLOSED requires half_open_successes consecutive successes to recover

- **Law**: the circuit closes only after `half_open_successes` consecutive
  successes in HALF_OPEN; a success in CLOSED resets the failure counter.
- **Negation**: a single success in HALF_OPEN closes the circuit.
- **Where verified**: `test_closes_after_required_successes`,
  `test_success_resets_failure_count`.

### CB5 — CLOSED trips OPEN on failure_threshold failures within failure_window

- **Law**: `failure_threshold` failures within the sliding `failure_window`
  trip the circuit; failures older than the window are pruned and do not
  count.
- **Negation**: stale failures count, or the threshold never trips.
- **Where verified**: `test_opens_after_threshold_failures`,
  `test_old_failures_pruned`.

---

## SessionPolicy (`src/cortex/sessions/policy.py`)

### SP1 — an ENDED session is terminal

- **Law**: an ENDED session is never handed back as a resumable session;
  `get_or_create_session` and `resume_session` treat ENDED as absent and
  create/return a fresh ACTIVE session instead.
- **Negation**: an ENDED session is returned for continued use.
- **Where verified**: `test_returns_none_for_ended_session` (resume),
  `test_ended_session_is_terminal_creates_new` (get_or_create, added
  2026-08-06). **This was a real bug**: `get_or_create_session` returned the
  ENDED session unchanged; fixed in 0012 by filtering `ENDED` before reuse.

### SP2 — created sessions are immediately ACTIVE

- **Law**: `create_session` and `get_or_create_session` (create path) return a
  session in ACTIVE state, never CREATED.
- **Negation**: a session is created but left in CREATED.
- **Where verified**: `test_creates_then_marks_active`,
  `test_creates_new_when_id_is_none`.

### SP3 — IDLE sessions auto-resume on first use

- **Law**: adding a message to or resuming an IDLE session transitions it to
  ACTIVE; an ACTIVE session is left unchanged.
- **Negation**: an IDLE session stays idle while being used.
- **Where verified**: `test_auto_resumes_idle_session`,
  `test_resumes_idle_session`, `test_passes_through_active_session_unchanged`.

### SP4 — end_session is idempotent and stamps the timestamp

- **Law**: `end_session` sets state ENDED with a non-null `ended_at`.
- **Negation**: an ended session lacks a timestamp or stays active.
- **Where verified**: `test_marks_ended_with_timestamp`.

---

## Governance

- New domain laws for touched modules MUST be appended here (or to the
  `## System Invariants` section of the relevant spec) and pinned with a
  failing-capable test, per the workflow's anti-vacuity rule.
- When a module has no domain laws, the skip annotation on the ticket must
  state "no domain laws" explicitly.
