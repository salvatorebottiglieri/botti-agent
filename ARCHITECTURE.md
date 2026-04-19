# Architecture Decision Record — cortex

> Collaborative planning document. Update as decisions are made.

---

## Overview

**Purpose:** Personal AI assistant that learns user patterns, delegates coding and non-coding tasks, and evolves through interaction.

**Vision:** System should be like water that flows into a river — no rigid boundaries, modules emerge and cooperate as the flow requires. An ecosystem of modules, not a single agent entity.

**Input Sources:** Two streams feed Cortex:
- **Direct input** — user chat, tools, goals (traditional interaction)
- **Sensory input** — minions (organs) that stream life data: location, payments, activity, etc.

**Language:** Python
**Key Libraries:** Pydantic, AsyncIO, Redis (event bus)
**Deployment:** Docker (multi-container)

---

## Core Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     INPUT SOURCES                                │
│                                                                  │
│  ┌──────────────────┐          ┌──────────────────────────────┐│
│  │  Traditional     │          │  MINIONS (organs)             ││
│  │  - Chat          │          │  - Phone (location, activity)  ││
│  │  - Tools         │          │  - Card (payments)            ││
│  │  - Goals         │          │  - Laptop (screen time, etc.) ││
│  └────────┬─────────┘          └──────────────┬─────────────────┘│
│           │                                    │                  │
└───────────┼────────────────────────────────────┼──────────────────┘
            │                                    │
            │ (events)                           │ (encrypted events)
            ▼                                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                         Event Bus                                 │
│            "The River" — indirect communication                   │
│  user.message │ payment │ location │ activity │ goal.* │ etc.  │
└──────┬────────────┬─────────────┬─────────────┬─────────────────┘
       │            │             │             │
       ▼            ▼             ▼             ▼
┌────────────┐ ┌──────────┐ ┌─────────────┐ ┌─────────────────┐
│Interaction │ │  Memory   │ │  Learning   │ │  Tool           │
│  Module    │ │  Module   │ │  Module     │ │  Ecosystem      │
│            │ │  (facts)  │ │(patterns)   │ │                 │
└────────────┘ └──────────┘ └─────────────┘ └─────────────────┘
       │                                         │
       ▼                                         ▼
┌───────────────┐                       ┌───────────────┐
│   Chat        │                       │   Execution   │
│   Interface   │                       │   Module      │
│ (query/insight)│                      │(orchestrator)│
└───────────────┘                       └───────────────┘
```

### Principles

1. **Indirect communication** — modules never call each other directly; events flow through the bus
2. **Learning is central** — every event feeds the learning loop
3. **Patterns → Preferences → Recommendations → Actions**
4. **Dynamic** — modules can spawn/terminate based on needs
5. **Extensible tools** — add capabilities via registry, no core code changes

---

## Event System

> The River — all modules communicate via events flowing through a shared bus (Redis Pub/Sub).

### Event Schema

```python
Event {
    type: str          # e.g., "user.message"
    payload: dict      # event-specific data
    metadata: EventMetadata {
        timestamp: datetime,
        session_id: str | None,
        source_module: str,
        trace_id: str,
        salience: float  # 0.0-1.0, filters low-importance events
    }
}
```

### Core Event Types

| Event | Emitted By | Consumed By | Purpose |
|-------|------------|-------------|---------|
| **User Input** | | | |
| `user.message` | API Gateway | Interaction, Memory, Learning | Incoming user input |
| `conversation.message` | Interaction | Memory, Learning | Agent responses |
| `conversation.ended` | Interaction | Memory, Learning | Session cleanup trigger |
| **Minion Input (sensory)** | | | |
| `location` | Phone Minion | Memory, Learning | GPS coordinates, venue |
| `payment` | Card Minion | Memory, Learning | Payment/transaction data |
| `activity` | Laptop Minion | Memory, Learning | Screen time, app usage |
| `calendar` | Phone Minion | Memory, Learning | Calendar events |
| `call_log` | Phone Minion | Memory, Learning | Incoming/outgoing calls |
| `app_usage` | Phone Minion | Memory, Learning | App usage summary |
| **Learning Output** | | | |
| `pattern.detected` | Learning | Interaction, Execution | Behavioral pattern found |
| `preference.learned` | Learning | Interaction, Execution | User preference updated |
| `recommendation.generated` | Learning | Interaction, Execution | Proactive suggestion |
| `recommendation.executed` | Execution | Learning (feedback loop) | Action was taken |
| **Tool/Goal** | | | |
| `tool.request` | Interaction, Execution | Tool Ecosystem | Execute a tool |
| `tool.result` | Tool Ecosystem | Requester | Tool execution result |
| `goal.created` | Interaction | Execution, Learning | New task goal |
| `goal.status` | Execution | Interaction, Learning | Goal progress update |
| `goal.completed` | Execution | Interaction, Learning | Task finished |
| `goal.failed` | Execution | Interaction, Learning | Task failed |
| `goal.resumed` | Execution | Interaction, Learning | Task recovered after crash |
| **Orchestration** | | | |
| `module.spawn` | Execution | (orchestration) | Spawn sub-process/worker |
| `module.terminate` | Execution | (orchestration) | Clean up sub-process |

**Minion events** flow through the same event bus as direct user input. Memory and Learning modules automatically process them to extract facts and patterns.

**Fact storage:** Facts are stored directly to Graph DB (TBD). Modules query the DB directly — no `fact.query`/`fact.result` events.

---

## Module Ecosystem

### Design Principles

- **Peers, not hierarchy** — modules are equal participants
- **Own their own state** — each module manages its internal state, persists to shared DB
- **Reactive + Proactive** — respond to events AND initiate based on internal logic
- **Shared event bus** — Redis pub/sub for real-time coordination
- **Shared persistence** — Postgres/SQLite for sessions, facts, patterns, tool registry

---

### Interaction Module

> Chat interface with the user. Entry point for conversation, query interface for insights.

| Aspect | Decision |
|--------|----------|
| Subscribes to | `user.message`, `recommendation.generated` |
| Emits | `conversation.message`, `goal.created` |
| Owns LLM | Yes — generates responses, decides actions |
| State | Session context, current mode, conversation history |

**Responsibilities:**
- Parse user input → emit `user.message` event
- Render responses to user (text, tool results, recommendations)
- Manage conversation lifecycle (start, end, mode switches)
- Decide when to create goals vs respond directly
- **Query mode:** User can ask about their own life ("where do I spend most of my time?", "summarize my spending this month")

---

### Memory Module

> Persistent storage of facts and knowledge about the user and their world. Powered by minion sensory data.

| Aspect | Decision |
|--------|----------|
| Subscribes to | All events (`*`) — watches everything |
| Emits | (writes to Graph DB directly) |
| Owns LLM | Yes — fact extraction and synthesis |
| State | Facts DB (Graph DB TBD), user knowledge graph |

**Responsibilities:**
- Store and retrieve facts (user preferences, project context, people, history)
- Index facts for fast retrieval
- Fact extraction from minion data: location → "user works at X", payment → "user spent Y at Z"
- Fact extraction from conversations

**Minion data → Facts examples:**
| Minion Event | Extracted Fact |
|-------------|----------------|
| `location` (repeated, same place, 9-5) | "user works at [venue]" |
| `location` (night, same place) | "user lives at [venue]" |
| `payment` | "user spent $X at [merchant]" |
| `payment` (monthly, same merchant) | "user has subscription to [service]" |
| `activity` (low screen time on weekends) | "user is less active on screens weekends" |

**Data model:** Deferred — will be defined in Phase 2 (MVP uses simple storage).

---

### Learning Module

> Pattern extraction, preference inference, proactive recommendations. Powered by rich minion data.

| Aspect | Decision |
|--------|----------|
| Subscribes to | All events (`*`) — watches everything |
| Emits | `pattern.detected`, `preference.learned`, `recommendation.generated` |
| Owns LLM | Yes — pattern analysis, preference synthesis |
| State | Pattern store, preference store, recommendation history |

**Responsibilities:**
- Extract behavioral patterns from event streams (user chat AND minion data)
- Infer user preferences from repeated behaviors
- Generate proactive recommendations based on learned patterns
- Provide feedback loop: track if recommendations were acted upon

**Pattern types (powered by minion data):**
- **Temporal** — "user usually deploys on Friday afternoons"
- **Behavioral** — "user prefers concise responses when coding"
- **Spatial** — "user is at home on weekends, at office on weekdays"
- **Financial** — "user spends more at restaurants on Fridays"
- **Contextual** — "user asks about this project when it's 9pm"

**Minion-powered learning examples:**
| Minion Data | Learned Pattern |
|------------|----------------|
| `location` (daily, 9-5, same building) | "user works at [venue]" |
| `location` (evenings, nights) | "user lives at [venue]" |
| `payment` (monthly, same amount) | "user has subscription: [service]" |
| `payment` (Friday nights, restaurant) | "user dines out on Fridays" |
| `activity` (screen time patterns) | "user is most productive in mornings" |

**Recommendation loop:**
```
1. Pattern detected → stored as pattern.*
2. Preference inferred → stored as preference.*
3. Recommendation generated → emitted as recommendation.generated
4. Recommendation shown to user OR executed by Execution
5. Feedback tracked → recommendation.executed → Learning updates model
```

---

### Tool Ecosystem

> Extensible registry of tools/capabilities. Modules request tools, Tool Ecosystem executes.

| Aspect | Decision |
|--------|----------|
| Subscribes to | `tool.request` |
| Emits | `tool.result` |
| Owns LLM | No (or optional — for complex tool orchestration) |
| State | Tool registry (DB), tool definitions |

**Tool Registry:**
```python
Tool {
    id: UUID
    name: str
    description: str
    input_schema: JSONSchema
    output_schema: JSONSchema
    permissions: list[str]
    category: str       # file | shell | search | api | custom
    registered_at: datetime
    active: bool
}
```

**Discovery mechanism:**
- Tools registered at startup (from config/DB)
- Tools can self-register via `tool.register` event
- Modules discover tools via `tool.search` event or querying DB directly

**Execution:**
- Tool Executor receives `tool.request`
- Validates input against `input_schema`
- Checks permissions
- Executes with timeout
- Returns `tool.result`

**Adding new tools:**
1. Implement tool class (extending `Tool` base)
2. Register in tool registry (DB or config)
3. Tool becomes available to all modules — no core code changes

---

### Execution Module

> Task orchestration. Spawns workers/sub-processes for complex or long-running tasks.

| Aspect | Decision |
|--------|----------|
| Subscribes to | `goal.created`, `recommendation.executed` |
| Emits | `goal.status`, `goal.completed`, `module.spawn` |
| Owns LLM | No (orchestration is deterministic) |
| State | Active goals, spawned processes |

**Responsibilities:**
- Receive goals from Interaction Module
- Break down goals into sub-tasks
- Spawn workers/sub-processes as needed
- Track goal progress → emit `goal.status` events
- Coordinate multiple concurrent goals

**Dynamic spawning:**
- Can spawn temporary worker processes for complex tasks
- Workers communicate via event bus (not direct IPC)
- Execution Module tracks lifecycle → `module.spawn` / `module.terminate`

---

## LLM Abstraction Layer

> Each module that needs LLM has its own client instance.

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Interface design | Abstract class `LLMClient` with `chat()` method | Provider-agnostic |
| Response model | `ChatResult` with `message` + optional `tool_calls` | Unified text + tools |
| Async support | Async from day 1 | I/O-bound operations |
| Per-module instance | Yes — each module owns its client | Independent scaling |
| Generation config | Per-call override | Flexibility |
| Tool definition | Provider-agnostic internal schema, translated on-the-fly | Swap providers without rewrites |

**Module-LLM mapping:**

| Module | Has LLM? | Purpose |
|--------|----------|---------|
| Interaction | Yes | Response generation, action decisions |
| Memory | Yes | Fact extraction, knowledge synthesis |
| Learning | Yes | Pattern analysis, preference inference |
| Tool Ecosystem | No | Tool execution (deterministic) |
| Execution | No | Orchestration (deterministic) |

**LLM Resource Management:**

```
┌─────────────────────────────────────────┐
│          LLMResourceManager              │
│                                         │
│  Priority queue:                        │
│    0 (highest): Interaction             │
│    1 (medium): Memory                   │
│    2 (lowest): Learning                 │
└─────────────────────────────────────────┘
```

Modules request LLM access via `LLMResourceManager`. Higher priority waits less. On 429, exponential backoff.

**On crash recovery:** Execution Module reads in-flight goals from DB, emits `goal.resumed` for each.

**Module lifecycle:** Each module exposes `/health` endpoint. Orchestrator polls for health checks.

---

## Translation Layer

> Tool definitions flow as provider-agnostic internal schema, translated at call time.

```
Internal Tool (canonical)
    │
    │  LLMClient.translate_tools()
    ▼
Provider-specific format
    │
    │  LLM call → ToolCall response
    ▼
Internal ToolCall (canonical)
    │
    │  LLMClient.translate_tool_call()
    ▼
Provider-specific tool call format
```

Each `LLMClient` handles:
- `ToolDefinition` → provider format serialization
- Provider `function_call` → `ToolCall` deserialization
- `ToolResult` → provider continuation format

---

## Error Handling

| Error Type | Handling |
|------------|----------|
| Transient (429, 503) | Retry with exponential backoff |
| Auth failures (401, 403) | Surface to responsible module |
| Invalid requests (400, 422) | Surface to responsible module |
| Tool execution error | Return as `tool.result` with error flag; retry once |
| Module crash | Other modules continue; event bus handles reconnect |
| Unrecoverable failure | Log, alert, graceful degradation |

---

## Persistence

| Store | Technology | Purpose |
|-------|------------|---------|
| Sessions | SQLite (v1) → Postgres | Conversation history, session metadata |
| Facts | Postgres (with vector search later) | User knowledge base |
| Patterns | Postgres | Learned behavioral patterns |
| Preferences | Postgres | Inferred user preferences |
| Tool Registry | Postgres / Config YAML | Available tools and schemas |
| Recommendations | Postgres | Recommendation history (feedback loop) |

**Event bus (Redis)** is for real-time coordination, NOT persistence. All state survives restarts via Postgres/SQLite.

---

## Observability

| Aspect | Decision |
|--------|----------|
| Logging | Structured JSON logs (per module) |
| Log levels | DEBUG, INFO, WARNING, ERROR (configurable) |
| Tracing | Distributed tracing via `trace_id` in event metadata |
| Metrics | Deferred — add later when needed |

---

## Security & Sandboxing

| Aspect | Decision |
|--------|----------|
| File operations | Sandboxed, configurable allowed paths |
| Shell execution | Restricted shell, no interactive sudo |
| Network access | Per-tool controls |
| Tool permissions | Tool-level access control |
| Secrets | Env vars, never hardcoded |

---

## Streaming

Deferred — not day 1. MVP first.

---

## Configuration

| Decision | Choice |
|----------|--------|
| Config source | YAML + env var overrides |
| Runtime reload | No — restart required |
| Secrets | Env vars |
| Library | Pydantic Settings |

---

## Settled Questions

- **Architecture:** Event-driven ecosystem (river metaphor), modules as peers, indirect communication via event bus
- **Input streams:** Two — Direct input (chat, tools, goals) and Sensory input (minions as organs)
- **Minions:** Organs that stream life data; phones, cards, laptops send encrypted events to Cortex
- **Event bus:** Redis Pub/Sub + DB-backed fallback — all inter-module communication flows through events
- **Event schema:** Versioned Pydantic models for core events; BaseEvent + per-type payloads
- **Salience mechanism:** Events carry salience score (0.0-1.0); modules filter by threshold
- **Event naming:** Session in metadata only, no wildcard subtypes in event types
- **Module interfaces:** Pure events for all communication; each module subscribes/publishes defined events
- **Fact storage:** Graph DB (TBD); modules query directly, no fact.query/fact.result events
- **Persistence:** Postgres/SQLite for state; event bus for real-time coordination
- **LLM ownership:** Per-module (Interaction, Memory, Learning each have their own)
- **LLM resource management:** Priority queue — Interaction > Memory > Learning
- **Tool correlation:** `correlation_id` in tool.request/result payloads
- **Goal lifecycle:** goal.created → goal.status → goal.completed / goal.failed / goal.resumed
- **Module lifecycle:** Health checks via `/health` endpoint, orchestrator polls
- **Tool extensibility:** Registry-based — register tools in DB, no core code changes
- **Dynamic spawning:** Yes — Execution Module can spawn workers for complex tasks
- **Learning scope:** Both preferences AND knowledge; proactive recommendations powered by minion data
- **Memory role:** Proactive transformer — watches all events (user + minion), extracts facts autonomously
- **Config:** YAML + env overrides, Pydantic Settings

---

## Event Schema

> Design completed: 2026-04-18

### Design Principles

- Versioned schemas (`version` field in BaseEvent) for backward compatibility
- Pydantic models for all event payloads — validation at emit/consume
- Core events only — avoid over-engineering, iterate as needed
- Salience-based filtering — modules ignore events below their threshold

### Base Event

```python
class BaseEvent(BaseModel):
    type: str                           # e.g., "user.message"
    version: str = "1.0"               # schema version
    metadata: EventMetadata             # shared across all events
    payload: dict                       # event-specific

class EventMetadata(BaseModel):
    timestamp: datetime
    session_id: str | None
    source_module: str
    trace_id: str
    salience: float = 0.5              # 0.0-1.0, filters low-importance events
```

### Salience Scoring

Each module defines a `min_salience` threshold. Events below this are ignored.

| Event | Default Salience | Rationale |
|-------|-----------------|-----------|
| `user.message` | 0.8 | Direct user input |
| `goal.created` | 0.9 | Explicit task delegation |
| `goal.completed` | 0.7 | Task done, learning feedback |
| `recommendation.executed` | 0.7 | User acted on suggestion |
| `conversation.message` | 0.5 | Internal dialogue |
| `tool.request` | 0.6 | Tool execution |
| `tool.result` | 0.5 | Result return |
| `fact.extracted` | 0.3 | Background storage |
| `pattern.detected` | 0.4 | Background insight |
| `preference.learned` | 0.4 | Background learning |
| `recommendation.generated` | 0.5 | Needs user action to matter |
| `goal.status` | 0.4 | Progress update |
| `module.spawn` | 0.6 | System orchestration |

Content-based boost: events containing urgency keywords ("urgent", "error", "fail") get +0.2 salience boost.

### Core Event Payloads

| Event | Key Payload Fields |
|-------|-------------------|
| `user.message` | `content: str`, `mode: str`, `attachments: list` |
| `conversation.message` | `content: str`, `sender: str`, `tool_calls: list`, `recommendations: list` |
| **Minion events** | | |
| `location` | `latitude: float`, `longitude: float`, `venue: str | None`, `accuracy: float`, `minion_id: str` |
| `payment` | `amount: float`, `currency: str`, `merchant: str`, `category: str`, `timestamp: datetime` |
| `activity` | `app: str`, `duration_seconds: int`, `timestamps: list[datetime]` |
| `calendar` | `event: str`, `start: datetime`, `end: datetime`, `location: str | None` |
| `call_log` | `direction: str`, `contact: str | None`, `duration_seconds: int`, `timestamp: datetime` |
| `app_usage` | `app: str`, `duration_seconds: int`, `date: date` |
| **Tool/Goal events** | | |
| `tool.request` | `correlation_id: UUID`, `tool_id: UUID`, `tool_name: str`, `arguments: dict`, `requester: str` |
| `tool.result` | `correlation_id: UUID`, `tool_id: UUID`, `tool_name: str`, `success: bool`, `result: dict`, `error: str` |
| `goal.created` | `goal_id: UUID`, `description: str`, `priority: str`, `deadline: datetime` |
| `goal.status` | `goal_id: UUID`, `status: str`, `progress_percent: int` |
| `goal.completed` | `goal_id: UUID`, `result: dict`, `duration_seconds: int` |
| `goal.failed` | `goal_id: UUID`, `error: str`, `failed_at: datetime` |
| `goal.resumed` | `goal_id: UUID`, `resumed_at: datetime` |
| **Learning events** | | |
| `recommendation.generated` | `recommendation_id: UUID`, `type: str`, `content: str`, `confidence: float`, `related_facts: list[UUID]` |

### Example: tool.request Full Schema

```python
class ToolRequestPayload(BaseModel):
    correlation_id: UUID       # for correlating request with result
    tool_id: UUID
    tool_name: str
    arguments: dict
    requester_module: str
    timeout: int | None = None

class ToolRequestEvent(BaseEvent):
    type: Literal["tool.request"]
    metadata: EventMetadata
    payload: ToolRequestPayload
```

### Event Schema Locations

```
src/cortex/
├── events/
│   ├── __init__.py
│   ├── base.py           # BaseEvent, EventMetadata
│   ├── schemas.py        # All event payload Pydantic models
│   └── registry.py       # Event type constants, validation utilities
```

**Status:** ✅ Design agreed

---

## Module Interfaces

> Design completed: 2026-04-18 (evening)

### Design Principles

- **Pure events** — all communication goes through event bus, even same-process
- **Salience filtering** — modules ignore events below their `min_salience` threshold
- **Subscribers** — events each module listens to (inputs)
- **Publishers** — events each module emits (outputs)
- **Internal API** — key components within each module

### Interaction Module

**Purpose:** Entry/exit point for user conversation.

```
Subscriptions:
├── user.message                     # incoming user messages
└── recommendation.generated         # proactive suggestions to present to user

Publications:
├── conversation.message             # agent responses
└── goal.created                     # when user delegates a task
```

**Internal Components:**
```python
InteractionService    # orchestrates conversation flow
ResponseRenderer       # formats responses (text, tool results, recommendations)
SessionManager        # manages session context, current mode per session
```

---

### Memory Module

**Purpose:** Persistent storage of facts and knowledge about the user.

```
Subscriptions:
├── user.message                     # all user messages — extract facts
└── conversation.message             # agent responses — extract facts

Database:
└── Graph DB (TBD)                   # facts stored directly, queried by other modules

Note: Modules query Graph DB directly — no fact.query/fact.result events.
```

**Internal Components:**
```python
FactGraphDB            # Graph DB client for direct queries
FactExtractor          # LLM client — extracts structured facts from text
```

**Fact categories:** `preference`, `behavior`, `knowledge`, `context`

---

### Learning Module

**Purpose:** Pattern detection, preference inference, proactive recommendations.

```
Subscriptions:
├── user.message                     # watch all user input
├── conversation.message             # watch all agent responses
├── goal.completed                   # learn from completed tasks
├── goal.failed                      # learn from failures
└── recommendation.executed          # feedback loop — was recommendation acted on?

Publications:
├── pattern.detected                  # new behavioral pattern found
├── preference.learned               # user preference inferred
└── recommendation.generated         # proactive suggestion
```

**Internal Components:**
```python
PatternAnalyzer         # detects temporal/behavioral patterns from events
PreferenceEngine        # infers preferences from repeated patterns
Recommender             # generates actionable recommendations
FeedbackTracker         # closes the loop: track recommendation outcomes
```

---

### Tool Ecosystem

**Purpose:** Dynamic tool registry and execution.

```
Subscriptions:
└── tool.request                     # execution requests

Publications:
└── tool.result                       # execution results (success or failure)

Note: tool.result includes correlation_id to match with requester.
```

**Internal Components:**
```python
ToolRegistry             # DB-backed registry of all available tools
ToolValidator            # validates input against tool's input_schema
ToolExecutor             # runs tool with timeout, returns result
```

**Tool Registry Schema:**

```python
class Tool(BaseModel):
    id: UUID
    name: str
    description: str
    version: str                      # e.g., "1.0.0"
    input_schema: JSONSchema          # Pydantic-compatible JSON Schema
    output_schema: JSONSchema | None
    permissions: list[str]            # e.g., ["file:read", "shell:execute"]
    category: str                     # file | shell | search | api | custom
    handler: str                      # fully qualified class name
    active: bool = True
    registered_at: datetime
    updated_at: datetime
```

**Registration:** Tools register as classes at startup (from config) or runtime. Modules query `ToolRegistry` directly to discover available tools.

**Adding a new tool:**
1. Create tool class extending `Tool` base
2. Register via config.yaml or runtime call
3. No core code changes — Tool Ecosystem discovers and executes

---

### Execution Module

**Purpose:** Goal orchestration and dynamic worker spawning.

```
Subscriptions:
├── goal.created                     # new tasks to execute
└── recommendation.executed          # recommendations that were acted on

Publications:
├── goal.status                      # progress updates
├── goal.completed                   # task finished
├── goal.failed                      # task failed
├── goal.resumed                     # task recovered after crash
└── module.spawn                     # spawn a worker process
```

**Internal Components:**
```python
GoalOrchestrator         # manages goal lifecycle, breaks into sub-tasks
WorkerSpawner            # dynamically spawns worker processes
ProgressTracker          # emits goal.status updates
```

**Crash recovery:** On startup, reads in-flight goals from DB, emits `goal.resumed` for each.

---

### Interface Summary

| Module | Input Events | Output Events | Internal API |
|--------|-------------|---------------|--------------|
| Interaction | `user.message`, `recommendation.generated` | `conversation.message`, `goal.created` | `InteractionService`, `ResponseRenderer` |
| Memory | `user.message`, `conversation.message` | (writes to Graph DB directly) | `FactGraphDB`, `FactExtractor` |
| Learning | `user.message`, `conversation.message`, `goal.completed`, `goal.failed`, `recommendation.executed` | `pattern.detected`, `preference.learned`, `recommendation.generated` | `PatternAnalyzer`, `PreferenceEngine`, `Recommender` |
| Tool Ecosystem | `tool.request` | `tool.result` | `ToolRegistry`, `ToolExecutor` |
| Execution | `goal.created`, `recommendation.executed` | `goal.status`, `goal.completed`, `goal.failed`, `goal.resumed`, `module.spawn` | `GoalOrchestrator`, `WorkerSpawner` |

---

## Project Structure

> Design completed: 2026-04-19

### Directory Layout

```
cortex/
├── src/
│   └── cortex/
│       ├── __init__.py
│       ├── config.py                 # Pydantic Settings (global config)
│       │
│       ├── api/                      # API Gateway (entry point)
│       │   ├── __init__.py
│       │   └── routes.py             # /chat, /health endpoints
│       │
│       ├── interaction/              # Interaction Module
│       │   ├── __init__.py
│       │   ├── service.py
│       │   └── renderer.py
│       │
│       ├── memory/                   # Memory Module
│       │   ├── __init__.py
│       │   ├── graph_db.py           # Graph DB client
│       │   └── extractor.py          # LLM fact extraction
│       │
│       ├── learning/                 # Learning Module
│       │   ├── __init__.py
│       │   ├── patterns.py
│       │   ├── preferences.py
│       │   ├── recommender.py
│       │   └── feedback.py
│       │
│       ├── tools/                    # Tool Ecosystem
│       │   ├── __init__.py
│       │   ├── registry.py
│       │   ├── executor.py
│       │   └── meta/                 # Meta tools
│       │       ├── file_read.py
│       │       ├── file_write.py
│       │       ├── shell.py
│       │       ├── grep.py
│       │       └── http_request.py
│       │
│       ├── execution/                # Execution Module
│       │   ├── __init__.py
│       │   ├── orchestrator.py
│       │   └── worker.py
│       │
│       ├── llm/                      # LLM Abstraction
│       │   ├── __init__.py
│       │   ├── base.py               # LLMClient abstract class
│       │   ├── resource_manager.py   # Priority queue
│       │   └── clients/              # Provider implementations
│       │       ├── __init__.py
│       │       └── openai.py
│       │
│       └── events/                    # Event System
│           ├── __init__.py
│           ├── base.py               # BaseEvent, EventMetadata
│           ├── schemas.py            # All event payload models
│           ├── bus.py                # Redis event bus
│           └── fallback.py           # DB-backed fallback
│
├── tests/
│   ├── unit/                        # Per-module unit tests
│   │   ├── interaction/
│   │   ├── memory/
│   │   └── ...
│   └── integration/
│
├── config.yaml                       # Configuration
├── Dockerfile                        # Per-module Dockerfiles
├── docker-compose.yml
└── pyproject.toml
```

### Design Principles

- **By-module organization** — each module is a top-level Python package
- **Self-contained modules** — each module has its own tests
- **Shared code at top level** — events, llm, config shared across modules
- **Per-module Docker** — each module can be built and scaled independently

**Status:** ✅ Design agreed

---

## Docker Deployment

> Design completed: 2026-04-19

### Containers

| Container | Module | Image |
|-----------|--------|-------|
| `cortex-api` | API Gateway | `cortex/api` |
| `cortex-interaction` | Interaction Module | `cortex/interaction` |
| `cortex-memory` | Memory Module | `cortex/memory` |
| `cortex-learning` | Learning Module | `cortex/learning` |
| `cortex-tools` | Tool Ecosystem | `cortex/tools` |
| `cortex-execution` | Execution Module | `cortex/execution` |

### External Services (not in docker-compose)

| Service | Purpose |
|---------|---------|
| Redis | Event bus |
| PostgreSQL | Sessions, patterns, preferences, tool registry |
| Graph DB (TBD) | Facts storage |

### docker-compose.yml (proposed)

```yaml
version: '3.8'

services:
  api:
    build: ./src/cortex/api
    container_name: cortex-api
    ports:
      - "8000:8000"
    networks:
      - cortex-net
    depends_on:
      - redis
    environment:
      - REDIS_HOST=redis
      - POSTGRES_HOST=postgres

  interaction:
    build: ./src/cortex/interaction
    container_name: cortex-interaction
    networks:
      - cortex-net
    depends_on:
      - redis
      - postgres
    environment:
      - REDIS_HOST=redis
      - POSTGRES_HOST=postgres
      - LLM_PROVIDER=${LLM_PROVIDER}

  memory:
    build: ./src/cortex/memory
    container_name: cortex-memory
    networks:
      - cortex-net
    depends_on:
      - redis
      - postgres
    environment:
      - REDIS_HOST=redis
      - GRAPH_DB_HOST=${GRAPH_DB_HOST}

  learning:
    build: ./src/cortex/learning
    container_name: cortex-learning
    networks:
      - cortex-net
    depends_on:
      - redis
      - postgres
    environment:
      - REDIS_HOST=redis
      - POSTGRES_HOST=postgres
      - LLM_PROVIDER=${LLM_PROVIDER}

  tools:
    build: ./src/cortex/tools
    container_name: cortex-tools
    networks:
      - cortex-net
    depends_on:
      - redis
      - postgres
    environment:
      - REDIS_HOST=redis
      - POSTGRES_HOST=postgres

  execution:
    build: ./src/cortex/execution
    container_name: cortex-execution
    networks:
      - cortex-net
    depends_on:
      - redis
      - postgres
    environment:
      - REDIS_HOST=redis
      - POSTGRES_HOST=postgres

networks:
  cortex-net:
    driver: bridge
```

### Dockerfile Pattern (per module)

```dockerfile
# src/cortex/<module>/Dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY src/cortex/<module> /app/cortex/<module>
COPY src/cortex/shared /app/cortex/shared

RUN pip install -e .

CMD ["python", "-m", "cortex.<module>"]
```

**Status:** ✅ Design agreed

---

## Next Steps

1. [x] Define complete event schema (all event types and payloads)
2. [x] Design module interfaces (what each module exposes via events)
3. [x] Design tool registry schema and registration flow
4. [x] Sketch project structure and file layout
5. [x] Plan Docker multi-container deployment (docker-compose)
6. [ ] Define Pydantic models for persistence (sessions, facts, patterns)
7. [ ] Design Minion protocol (communication, encryption, transport)
8. [ ] Define minion event schemas (location, payment, activity, etc.)

---

## Minions

> Design pending — to be detailed in separate planning document.

### Concept

Minions are **organs** that stream sensory data to Cortex. They are processes running on user devices (phone, laptop, card reader) that collect and send specific data streams.

### Minion Types

| Minion | Device | Data Streamed |
|--------|---------|---------------|
| `phone_minion` | Android/iOS | `location`, `calendar`, `call_log`, `app_usage` |
| `card_minion` | Payment reader | `payment`, `refund` |
| `laptop_minion` | Desktop/Laptop | `activity`, `screen_time`, `app_usage` |

### Minion Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      MINION                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ Data        │  │ Filter &    │  │ Encrypted           │ │
│  │ Collectors  │→ │ Normalizer  │→ │ Event Publisher     │ │
│  │ (GPS, API)  │  │ (debounce,  │  │ (E2E encrypt)       │ │
│  │             │  │  dedup)     │  │                     │ │
│  └─────────────┘  └─────────────┘  └──────────┬──────────┘ │
└───────────────────────────────────────────────┼─────────────┘
                                                │ (HTTPS/MQTT)
                                                ▼
                                      ┌─────────────────┐
                                      │    CORTEX       │
                                      │   (brain)       │
                                      │                 │
                                      │ Event Bus       │
                                      │ (receives and   │
                                      │ processes)      │
                                      └─────────────────┘
```

### Key Properties

| Property | Value |
|----------|-------|
| Connection | Internet, E2E encrypted |
| Protocol | HTTPS push or MQTT |
| Data ownership | All data stays local (user's devices + user's Cortex instance) |
| Cortex role | Process only — no commands to minions (v1) |
| Minion autonomy | Minions decide what/when to send |

### Minion → Cortex Event Flow

1. Minion collects raw data (GPS, payment, etc.)
2. Minion filters/normalizes (debounce location, deduplicate payments)
3. Minion encrypts event payload
4. Minion sends via HTTPS/MQTT to Cortex endpoint
5. Cortex receives, decrypts, emits to event bus
6. Memory/Learning modules process automatically

---

*Last updated: 2026-04-19*
