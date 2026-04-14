# Architecture Decision Record — botti-agent

> Collaborative planning document. Update as decisions are made.

---

## Overview

**Purpose:** Full agent harness with LLM abstraction layer  
**Language:** Python  
**Key Libraries:** Pydantic, SDK for LLM interaction (TBD)  
**Deployment:** Docker  

---

## Core Architecture

```
┌─────────────────────────────────────────────────┐
│                  Agent Core                      │
│  (session管理, tool dispatch, prompt composition)  │
├──────────────┬──────────────┬───────────────────┤
│   Memory     │    Tools    │   Prompt Manager   │
├──────────────┴──────────────┴───────────────────┤
│           LLM Abstraction Layer                   │
│  (protocol: chat() → ChatResult)                │
├─────────────────────────────────────────────────┤
│   GeminiClient │ OpenAIClient │ BedrockClient... │
└─────────────────────────────────────────────────┘
```

---

## Decisions

### LLM Abstraction Layer

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Interface design | Abstract class `LLMClient` with `chat()` method | Provider-agnostic, easy to add new clients |
| Response model | `ChatResult` with `message` + optional `tool_calls` | Unified handling of text and tool-use modes |
| Async support | Async from day 1 | I/O-bound tool execution benefits most |
| Multi-provider | Single abstraction, multiple implementations | Gemini first, others later |
| Generation config | Defaults on provider, override per call (Option C) | Flexibility without complexity |
| Tool definition | Provider-agnostic internal schema, translated on-the-fly | Allows swapping LLM providers without rewriting tools |

**Status:** ✅ Mostly agreed — details pending

---

## Translation Layer

> Tool definitions flow through the system as provider-agnostic internal schema, translated to provider format at call time.

```
Internal Tool (canonical)
    │
    │  LLMClient.translate_tools()
    ▼
Provider-specific format
  - OpenAI: { name, description, parameters: schema }
  - Gemini: { function_declarations: [{ name, description, parameters }] }
    │
    │  LLM call → ToolCall response
    ▼
Internal ToolCall (canonical)
    │
    │  LLMClient.translate_tool_call()
    ▼
Provider-specific tool call format

Tool result:
  - LLMClient.translate_result() → internal ToolResult
  - ToolResult → provider's expected format for continuation
```

Each `LLMClient` implementation is responsible for:
- Serializing internal `ToolDefinition` → provider format
- Deserializing provider's `function_call` response → internal `ToolCall`
- Serializing internal `ToolResult` → provider's continuation format

**Status:** ✅ Core design decision agreed

---

## Agent Core

> Central orchestration layer. Ties together LLM client, tool system, memory/session, and prompt manager.

### Input Sources

| Source | Description |
|--------|-------------|
| REST API server | Interactive chat via HTTP, JWT auth |
| Event bus | Triggered by external events (user messages, timers, webhooks, task completions, monitoring alerts) |

Event subscriptions are configurable — user injects which events the agent listens to.

### Chain Goal Tracking

- Goals stored in session metadata
- Multiple parallel goals supported
- Lifecycle: create → active → completed/failed

### Agent Loop

```
Agent.run(input, session_id, source, mode):
    session = load_or_create_session(session_id)

    if source == "event":
        session.goal = extract_goal_from_event(input)
        session.status = "active"

    messages = build_messages(session, input, mode)

    while True:
        result = await llm.chat(messages, tools=meta_tools_for(mode))

        if result.message.content and not result.tool_calls:
            session.status = "completed"
            save_periodic(session)
            return Complete(result.message)

        if result.tool_calls:
            parallel = [tc for tc in tool_calls if tc.name in READ_TOOLS]
            sequential = [tc for tc in tool_calls if tc.name not in READ_TOOLS]

            parallel_results = await gather(*[execute(t) for t in parallel])
            for tc in sequential:
                messages.append((await execute(tc)).to_message())
            messages.extend(parallel_results.map(to_message))

        if iteration >= max_iterations:
            session.status = "incomplete"
            save_all_state(session)  # for post-mortem investigation
            return Incomplete(iteration, partial_state)

        if token_count > threshold_60_percent:
            inject_system_message(messages, "Context nearing limit. Consider pruning.")

        iteration += 1
```

**READ_TOOLS:** `file_read`, `grep`, `http_request` — executed in parallel
**SEQUENTIAL_TOOLS:** `file_write`, `shell` — executed in order

### Termination

| Outcome | Behavior |
|---------|----------|
| Complete | Text response, no tool calls; session status = "completed" |
| Incomplete | Max iterations, user interrupt, fatal error; save all state for post-mortem |
| LLM failure | Return error to user; do not continue |

### Error Handling

| Error Type | Handling |
|------------|----------|
| Auto-retryable (429, 503, transient) | Retry with backoff, configurable max retries |
| Agent-handleable (401, 400, 422, 500, timeout) | Surface to agent; let LLM decide |
| Tool execution error | Return as ToolResult; retry once, then surface to LLM |
| Unrecoverable LLM failure | Return error to user |

### Context Pruning

- Harness maintains token count across messages
- At 60% of context window, inject advisory system message
- Agent decides what to prune via harness-provided tools (`prune_context`, `summarize_recent`)

### Session Persistence

- SQLite (v1), Postgres (later)
- Background periodic save
- Full state preserved on incomplete termination for investigation

**Status:** ✅ Agreed

---

### Tool System

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Tool definition schema | Constrained JSON Schema subset | Simple to translate across providers; sufficient expressiveness |
| Tool interface | OOP class (`Tool` abstract base class) | Natural grouping of metadata + execution; easy to extend |
| Error model | Isolated — errors returned as `ToolResult`; timeout → agent | Consistent format; LLM can see and handle |
| Exception taxonomy | Agent-handleable vs Runtime-handled (distinct) | Agent can retry/adapt vs fatal/unrecoverable |
| Permission enforcement | Per-tool, strict | Each tool declares required permissions |
| Input validation | Strict — reject invalid before execution | Fail fast, clear error messages |
| Max iterations | Configurable guard | Prevent infinite loops |
| Recursion guard | Continue on partial failure, collect errors | Graceful degradation |

**Status:** ✅ Agreed

---

### Meta Tools

> Core building blocks always available to the agent; composable to handle any task.

**Default set:** `file_read`, `file_write`, `shell`, `grep`, `http_request`

| Decision | Choice |
|----------|--------|
| Configurable | Yes — loaded from config |
| Toggleable | Yes — per session (e.g., disable `shell` in planning mode) |
| Conditional loading | Mode-based exclusion (e.g., planning mode → no `file_write`, no `shell`) |
| Extensibility | Not expected to grow; current set is sufficient |

**Mode concept:** `planning` mode is a first-class harness concept used to dissect agent capabilities.

---

### Searchable Tools (External Entities)

> Tools for interacting with entities outside the system (GitHub API, DB, external services).

| Decision | Choice |
|----------|--------|
| Discovery mechanism | LLM-assisted via `search_tools` meta tool |
| Trigger | Agent self-initiates — explicitly calls `search_tools(query)` when meta tools insufficient |
| Loading | Both pre-registered (startup) and dynamically loaded (deferred) |

**Pre-built searchable tools:** Deferred — user-defined ecosystem.

---

### Tool Executor

```
Tool call flow:
1. LLM returns message + tool_calls
2. For each tool_call:
   a. Look up tool in registry
   b. Strict validation against input_schema
   c. Strict permission check (per-tool)
   d. Execute with arguments
   e. Timeout → agent (not caught)
   f. Other exceptions → ToolResult (error returned to LLM)
   g. Collect results
3. Append tool results as messages (role: tool_result)
4. Send back to LLM
5. Guard: max iterations (configurable)
```

**Status:** ✅ Agreed

---

### Memory & Session

| Decision | Choice |
|----------|--------|
| Message schema | Pydantic `Message` with `tool_result` as special role |
| Window strategy | Fixed message count (configurable) + agent advisory |
| Context overflow | Agent-driven at 60% token threshold; harness provides pruning/summarization tools, agent decides |
| Session persistence | SQLite (v1) → Postgres (later) |
| Session ID | `user_id + timestamp` → UUID |

**Session message flow:**
- Messages accumulate in session
- At ~60% token usage, agent instructed to proactively prune via harness-provided tools
- Fixed window is safety net; agent is primary context manager

**Status:** ✅ Agreed

---

### Mode

| Decision | Choice |
|----------|--------|
| Valid modes | `planning`, `execution` |
| Default mode | `execution` (long-running background tasks) |
| User injection | Interactive session — user toggles planning/execution |
| Effect on meta tools | Yes — mode switch changes available meta tools going forward |
| Effect on session history | No — history persists; agent loses forward access to tools excluded by mode |

**Mode behavior:**
- `planning`: Meta tools exclude `file_write`, `shell`; agent can still see prior history
- `execution`: All meta tools available

**Status:** ✅ Agreed

---

### Error Handling

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Auto-retry | Rate limits, model unavailables, traffic errors | Transient failures — retry with backoff |
| Surfaced to agent | Auth failures, context length, invalid requests, timeouts | Agent must handle explicitly |
| Retry policy | Exponential backoff, configurable max retries | Standard resilience pattern |
| Tool failure | Retry once, then surface to LLM | Fail fast but allow single recovery |

**Auto-retryable errors:** 429 (rate limit), 503 (unavailable), timeout (TBD)  
**Agent-handled errors:** 401/403 (auth), 400 (bad request), 422 (context length), 500 (server error)  
**Status:** ✅ Agreed (timeout TBD)

---

### Observability

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Logging | Structured JSON logs | Machine-parseable, CI-friendly |
| Log levels | DEBUG, INFO, WARNING, ERROR (configurable) | Verbosity control |
| Tracing | Span-based tracing for LLM calls and tool execution | Debug agent decision paths |
| Metrics | (Deferred — consider later) | Don't over-engineer upfront |

**Status:** ⏳ Pending

---

### Security & Sandboxing

| Decision | Choice | Rationale |
|----------|--------|-----------|
| File operations | Sandboxed by default, configurable allowed paths | Prevent destructive mistakes |
| Shell execution | Restricted shell, no interactive sudo | Contain damage |
| Network access | Per-tool controls | Defense in depth |
| Tool permissions | Tool-level access control | Fine-grained control |

**Status:** ⏳ Pending

---

### Streaming

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Streaming support | Implement later, not day 1 | Simplify initial implementation |
| Rationale | MVP first, streaming can be added without breaking architecture | YAGNI for complexity |

**Status:** ⏳ Pending

---

### Multi-Agent

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Multi-agent support | Not in initial scope | Keep initial harness focused |

**Status:** ⏳ Pending

---

### Configuration

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Config source | YAML/JSON config file + env var overrides | Self-documenting, supports commits, secrets via env |
| Runtime reload | No — restart required for config changes | Simplicity over sophistication |
| Secrets | Via env vars (never hardcoded) | Security |
| Library | Pydantic Settings | Type-checked, validated at startup |

**Config file:** `config.yaml` (or `.json`) with env var expansion for secrets  
**Status:** ✅ Agreed

---

## Open Questions

| Question | Options | Status |
|----------|---------|--------|
| REPL/CLI interface | CLI / API only / Both | Open |

**Settled:**
- LLM abstraction: abstract class, async, ChatResult, provider-agnostic tools
- Translation layer: internal ↔ provider on-the-fly
- Agent Core: REST API + event bus, chain goals, parallel/sequential tool execution, incomplete termination saves state
- Tool system: OOP class, strict validation, strict permissions, isolated errors
- Meta tools: configurable, toggleable, mode-based (planning excludes file_write, shell)
- Searchable tools: LLM-assisted via search_tools, both pre-registered and dynamic loading
- Memory: SQLite v1, fixed window + agent advisory, 60% threshold pruning
- Mode: planning/execution, user-injected, affects tool availability not history
- Config: YAML/JSON + env overrides, Pydantic Settings
- Session: user_id+timestamp UUID, background periodic save |

---

## Next Steps

1. [x] Finalize tool execution model
2. [x] Define Pydantic models for all core schemas (Message, ToolDefinition, ChatResult, etc.)
3. [x] Design session/memory boundaries
4. [x] Design Agent Core (loop, input sources, goal tracking, termination)
5. [ ] Design prompt manager and mode-based meta tool loading
6. [ ] Sketch project structure and file layout
7. [ ] Plan Docker build and entrypoint
8. [ ] Define all Pydantic schemas for persistence (session, message, tool_result)

---

*Last updated: 2026-04-14*