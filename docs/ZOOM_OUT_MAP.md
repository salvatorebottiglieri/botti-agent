# Cortex — Zoom-Out Map

> High-level map of all modules, callers, and dependencies using the project's domain vocabulary.
> Generated: 2026-05-13 (updated after personality-context + fixes commits)

---

## 🧠 System Overview

```
                     ┌──────────────────────────────────────┐
                     │          INPUT SOURCES               │
                     │                                      │
                     │  ┌──────────────┐  ┌──────────────┐ │
                     │  │  User (Chat   │  │  Minions     │ │
                     │  │  & Goals)    │  │  (Organs)    │ │
                     │  │  via HTTP    │  │  via MQTT    │ │
                     │  └──────┬───────┘  └──────┬───────┘ │
                     └─────────┼──────────────────┼──────────┘
                               │                  │
                ┌──────────────▼──────────────────▼──┐
                │         THE RIVER (Event Bus)       │
                │  Async pub/sub — indirect comms    │
                └──┬───────┬──────┬──────┬───────────┘
                   │       │      │      │
        ┌──────────▼┐ ┌────▼──┐ ┌─▼────┐ ┌▼──────────┐ ┌──────────┐
        │Interaction│ │Memory │ │Learn-│ │Execution   │ │Tool      │
        │ Module    │ │Module │ │ing   │ │Module      │ │Ecosystem │
        │(Thin I/O) │ │       │ │Module│ │(Agentic    │ │(Registry │
        │           │ │       │ │      │ │ Loop)      │ │+Executor)│
        └───────────┘ └───────┘ └──────┘ └────────────┘ └──────────┘
                              │                    │
                              ▼                    ▼
                     ┌────────────────┐   ┌────────────────┐
                     │  Postgres DB   │   │  LLM Providers │
                     │  (Facts,       │   │  (OpenAI)      │
                     │   Sessions,    │   └────────────────┘
                     │   Goals, etc)  │
                     └────────────────┘
```

---

## 🗺️ Module Map (all layers)

### Layer 0 — Foundation

| Component | Location | Purpose | Status |
|-----------|----------|---------|--------|
| **Config System** | `src/cortex/config/` | YAML + env var settings (Pydantic) | ✅ |
| **Logging** | `src/cortex/logging/` | Structured logging with trace_id propagation | ✅ |
| **Docker** | `Dockerfile`, `docker-compose.yml` | Multi-container (cortex + postgres + mosquitto) | ✅ |
| **DB Migrations** | `migrations/` | SQL migrations (sessions, facts, api_keys, goals) | ✅ |

### Layer 1 — Primitives

| Component | Location | Purpose | Used By | Status |
|-----------|----------|---------|---------|--------|
| **Event Bus (The River)** | `src/cortex/events/` | Async pub/sub with wildcards, error isolation | All modules | ✅ |
| **EventEmitter** | `src/cortex/events/emitter.py` | Safe publish seam (never raises, handles None bus) | LoopExecutor, ToolExecutorService, ExecutionModule | ✅ **NEW** |
| **LLM Client** | `src/cortex/llm/` | Provider-agnostic LLM abstraction (OpenAI today) | Reasoner, MemoryService, LearningModule | ✅ |
| **DB Pool** | `src/cortex/db/` | asyncpg connection pool + session context manager | All repositories | ✅ |

### Layer 2 — Standalone Modules

| Component | Location | ABC | Impl | Tests | Status |
|-----------|----------|-----|------|-------|--------|
| **Session Store** | `src/cortex/sessions/` | `SessionRepository` | `PostgresSessionRepository` | 30 | ✅ |
| **Session Policy** | `src/cortex/sessions/policy.py` | — (free functions) | `create_session`, `resume_session`, `add_user_message`, `end_session`, `get_or_create_session` | 7 | ✅ **NEW** |
| **Tool Registry** | `src/cortex/tools/` | `ToolRegistry`, `Tool` | `InMemoryToolRegistry` + 4 meta-tools | 67 | ✅ |
| **Minion MQTT** | `src/cortex/minions/` | `MinionGateway`, `MinionEventHandler` | `MinionMQTTClient` | 24 | ✅ |
| **Fact Store** | `src/cortex/memory/` | `FactRepository`, `ConceptRepository` | `PostgresFactRepository`, `PostgresConceptRepository` | 18 | ✅ **UPDATED** |

### Layer 3 — Service Layer

| Service | Location | Depends On | Wires Together | Status |
|---------|----------|------------|----------------|--------|
| **ToolExecutorService** | `src/cortex/services/tool_executor.py` | ToolRegistry, EventBus | Circuit breaker + EventEmitter + metrics | ✅ **UPDATED** |
| **MinionService** | `src/cortex/services/minion_service.py` | MinionGateway, MinionRegistry, EventBus, MemoryService | MQTT → events on bus | ✅ |
| **MemoryService** | `src/cortex/services/memory_service.py` | FactRepository, ConceptRepository, LLMClient, EventBus | Full query/storage/extraction API + **`get_memory_context()` bundle** | ✅ **UPDATED** |

### Layer 4 — Agentic Core

| Component | Location | Role | Status |
|-----------|----------|------|--------|
| **Context Builder** | `src/cortex/agentic/context_builder.py` | Assembles session history + memory bundle + tools; uses `SessionRepository` directly (no `SessionService`) | ✅ **UPDATED** |
| **Reasoner** | `src/cortex/agentic/reasoner.py` | LLM decision: respond, execute tools, ask question; reads `context.memory.*` bundle | ✅ |
| **Loop Executor** | `src/cortex/agentic/executor.py` | Executes tool calls (sequential/parallel); uses `EventEmitter` for events | ✅ **UPDATED** |
| **Agent Loop** | `src/cortex/agentic/loop.py` | Think → Act → Observe → Respond; 2 modes (chat/goal) | ✅ |
| **Context Model** | `src/cortex/agentic/models.py` | `Context.memory: MemoryContext` (bundles facts + personality + ambient + degraded_dimensions) | ✅ **UPDATED** |

### Layer 5 — Orchestration

| Module | Location | Subscribes To | Emits | Status |
|--------|----------|---------------|-------|--------|
| **ExecutionModule** | `src/cortex/execution/module.py` | `goal.created`, `recommendation.executed` | `goal.*` via EventEmitter | ✅ **UPDATED** |
| **InteractionService** | `src/cortex/interaction/service.py` | (called by API) | Uses `SessionRepository` + `policy` module | ✅ **UPDATED** |
| **PersonalityService** | `src/cortex/interaction/service.py` | (reads MemoryService) | Personality profile from MemoryContext | ✅ |
| **API Gateway** | `src/cortex/api/` | FastAPI routes | HTTP → InteractionService | ✅ |

### Layer 6 — Integration

| Package | Location | Role | Tests | Status |
|---------|----------|------|-------|--------|
| **cortex-protocol** | `src/cortex_protocol/` | Shared schemas (12 event types, MQTT topics, JSON Schema export) | 38 | ✅ |
| **App Bootstrap** | `src/cortex/main.py` | `initialize_app()` — wires everything; `CortexApp.session_repository` (was `session_service`) | — | ✅ **UPDATED** |
| **Laptop Minion** | `laptop-minion/` | Sensor collectors (screen, keyboard, network, battery) → MQTT | 4 | ✅ |

### Layer 7 — Learning Module (planned)

| Component | Location (planned) | Role | Status |
|-----------|--------------------|------|--------|
| **Encoder** | `src/cortex/learning/encoder.py` | MinionEvent → 64-dim feature vector | 📋 |
| **ESN Reservoir Engine** | `src/cortex/learning/engine.py` | Pure-NumPy Echo State Network | 📋 |
| **Ridge Readout** | `src/cortex/learning/readout.py` | Closed-form linear regression readout | 📋 |
| **Salience Labeler** | `src/cortex/learning/labeler.py` | Heuristic salience = 0.5·rarity + 0.3·hour_anomaly + 0.2·recency | 📋 |
| **Persistence** | `src/cortex/learning/persistence.py` | Readout weights as bytea in Postgres | 📋 |
| **LearningService** | `src/cortex/learning/service.py` | Stateful inference + offline batch training | 📋 |
| **CLI** | `src/cortex/learning/cli.py` | `python -m cortex.learning.cli train-salience` | 📋 |

---

## 🔀 Caller Map

```
User HTTP Request
    └── API Gateway (FastAPI routes)
        ├── POST /chat ──► InteractionService (facade)
        │                       └── [route calls ExecutionModule.run_chat() directly]
        │                            └── AgentLoop
        │                                 ├── ContextBuilder
        │                                 │    ├── SessionRepository.get_messages()
        │                                 │    ├── MemoryService.get_memory_context()   ← single bundle call
        │                                 │    │    ├── [facts]
        │                                 │    │    ├── [personality]
        │                                 │    │    └── [ambient]
        │                                 │    └── ToolRegistry.get_schemas()
        │                                 ├── Reasoner (LLMClient.chat())
        │                                 │    └── reads context.memory.facts / .personality / .ambient
        │                                 └── LoopExecutor.execute_tools()
        │                                      └── ToolExecutorService.execute()
        │                                           ├── CircuitBreaker (check → record)
        │                                           ├── EventEmitter.emit("tool.*")
        │                                           └── DefaultToolExecutor
        │                                                ├── FileReadTool
        │                                                ├── FileWriteTool
        │                                                ├── ShellTool
        │                                                └── GrepTool
        │
        ├── POST /goals ──► ExecutionModule.create_goal()
        │                       └── AgentLoop.run_goal()
        ├── /sessions/* ──► SessionRepository + policy functions
        ├── POST /admin/tokens ──► (DB: api_keys)
        └── GET /health ──► (checks DB, MQTT, LLM)

Minion MQTT Event
    └── MinionMQTTClient (subscribes to cortex/minions/+/events)
        └── MinionService.handle_event()
            └── EventBus.publish()
                └── MemoryService.handle_event()
                     └── (extracts facts → PostgresFactRepository)

App Bootstrap (initialize_app)
    └── Order:
        1. Config
        2. DB Pool
        3. Migrations
        4. Event Bus
        5. SessionRepository (PostgresSessionRepository)
        6. FactRepository + ConceptRepository (PostgresFactRepository, PostgresConceptRepository)
        7. LLM Client
        8. ToolRegistry + DefaultToolExecutor + ToolExecutorService
        9. MemoryService
        10. ContextBuilder (takes SessionRepository, MemoryService, ToolRegistry)
        11. Reasoner (takes LLMClient, ToolRegistry)
        12. LoopExecutor (takes ToolExecutor, EventBus)
        13. AgentLoop (takes ContextBuilder, Reasoner, LoopExecutor, EventBus)
        14. ExecutionModule (takes AgentLoop, EventBus — no GoalStore)
        15. PersonalityService + InteractionService
        16. API Gateway
        17. Subscribe services → EventBus
        18. MinionService (optional)
```

---

## 📦 Package Dependency Graph

```
pyproject.toml (cortex v0.1.0)
   ├── pydantic           — All models
   ├── asyncpg            — DB pool & sessions
   ├── openai             — LLM client (only provider today)
   ├── aiomqtt            — MQTT for minions
   ├── fastapi + uvicorn  — HTTP API
   ├── httpx              — Async HTTP (LLM)
   ├── structlog          — Logging
   ├── tenacity           — Retry/circuit breaker
   ├── pyyaml             — Config
   ├── typer              — CLI
   └── pytest + pytest-asyncio — Tests

laptop-minion/ (separate package, own pyproject.toml)
   ├── paho-mqtt          — MQTT publisher
   ├── psutil             — System sensors
   └── pydantic           — Config models
```

---

## 🧭 Key Architecture Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Communication** | **Indirect (Event Bus)** — "The River" | Modules never call each other directly; loose coupling |
| **Core Reasoning** | **Agentic Loop** (Think → Act → Observe → Respond) | Same engine powers chat and goals |
| **Memory** | **`get_memory_context()` bundle** | One call returns facts + personality + ambient + degraded flags; single seam |
| **Personality** | **Facts in Memory** (not separate store) | No duplication; PersonalityContext from MemoryService |
| **Session Layer** | **`SessionRepository` + policy functions** (no `SessionService` class) | Stateless policy on repository; less indirection |
| **Publishing Events** | **`EventEmitter` class** (never raises, handles None bus) | Single safe seam for all publish calls |
| **Tool Execution** | **Two-tier** — `ToolExecutorService` (circuit breaker + events) wraps `DefaultToolExecutor` (pure execution) | Separation of concerns |
| **Learning ML** | **Reservoir Computing** (Echo State Network, NumPy) | Cheaper than LLM per event; streaming-native; deterministic |
| **Minion Transport** | **MQTT** (Mosquitto, no TLS v1) | Persistent connection, QoS, offline buffering; trusted network |
| **API** | **FastAPI** | Async-native, auto-docs, Pydantic integration |
| **DB** | **asyncpg** (raw SQL, no ORM) | Async, no ORM overhead |
| **Interaction Module** | **Thin** — no LLM, no loop | I/O only; delegates to ExecutionModule's AgentLoop |

---

## 📊 Recent Changes (since last zoom-out)

| Change | Files | Impact |
|--------|-------|--------|
| 🆕 **EventEmitter** | `events/emitter.py`, `events/__init__.py` | Safe publish seam used by LoopExecutor, ToolExecutorService, ExecutionModule |
| 🆕 **Session Policy** | `sessions/policy.py` | Stateless policy functions (`create_session`, `resume_session`, etc.) replacing `SessionService` class |
| 🆕 **Fact Store impl** | `memory/repository.py` | `PostgresFactRepository` + `PostgresConceptRepository` (was just ABCs) |
| 🔄 **MemoryContext bundle** | `agentic/models.py`, `services/memory_service.py` | Single `get_memory_context()` call returns facts+personality+ambient+degraded flags |
| 🔄 **SessionService removed** | `sessions/service.py` deleted | `SessionRepository` + `policy` used everywhere instead |
| 🔄 **session_service → session_repository** | `main.py`, `context_builder.py`, `interaction/service.py` | All wiring now uses repository directly |
| 🆕 **GoalRepository** | `goals/repository.py`, `execution/module.py`, `main.py` | Goals persisted via GoalRepository (Postgres); in-flight goals resumed on startup (`resume_in_flight`) |
| 🔄 **Full CircuitBreaker** | `services/tool_executor.py` | Previously stub; now full implementation with CLOSED/OPEN/HALF_OPEN states |

---

## 📊 Wave Completion Status

| Wave | What | Tests | Status |
|------|------|-------|--------|
| **0** | Foundation (Config, Logging, Docker) | — | ✅ |
| **1** | Primitives (Event Bus, LLM Client, DB Pool) | 35 | ✅ |
| **2.1** | Session Store (models, interfaces, repository, **policy**) | 30 + 7 | ✅ **UPDATED** |
| **2.2** | Tool Registry + 4 meta-tools | 67 | ✅ |
| **2.3** | Minion MQTT (gateway, handler, registry) | 24 | ✅ |
| **2.4** | Fact Store (models, interfaces, **repository impl**) | 18 | ✅ **UPDATED** |
| **3** | Service Layer (ToolExecutor, MinionService, MemoryService) | — | ✅ **UPDATED** |
| **4** | Agentic Core (ContextBuilder, Reasoner, Executor, Loop) | 79 | ✅ **UPDATED** |
| **5** | Orchestration (ExecutionModule, InteractionService, API Gateway) | 39 | ✅ **UPDATED** |
| **6.1** | cortex-protocol (shared schemas) | 38 | ✅ |
| **6.2** | API Gateway (FastAPI routes, auth, CLI) | 11 | ✅ |
| **6.3** | App Bootstrap (CortexApp wiring) | — | ✅ **UPDATED** |
| **6.4** | Laptop Minion (sensors, MQTT) | 4 | ✅ |
| **7.1** | **Learning Module** — Reservoir + Salience Readout + CLI | *planned* | 📋 |

---

## 🔑 Entry Points

| Entry Point | File | Description |
|-------------|------|-------------|
| `cortex` (CLI) | `src/cortex/cli.py` | `cortex token:create`, `cortex version` |
| `cortex.main` | `src/cortex/main.py` | `initialize_app()` → `CortexApp` |
| `cortex.__main__` | `src/cortex/__main__.py` | `python -m cortex` |
| FastAPI app | `src/cortex/api/main.py` | `create_api_app()` — mounted by bootstrap |
| Laptop Minion | `laptop-minion/src/laptop_minion/main.py` | Standalone sensor process → MQTT |

---

## 📁 Source Tree (src/cortex/) — current state

```
src/cortex/
├── __init__.py
├── __main__.py              # python -m cortex
├── main.py                  # initialize_app() — wires everything
├── cli.py                   # Typer CLI (token:create, version)
│
├── agentic/                 # Wave 4 — Agentic Core
│   ├── __init__.py
│   ├── models.py            # Context, MemoryContext, Decision, Mode, Goal, ...
│   ├── context_builder.py   # Assembles reasoning context (uses SessionRepository)
│   ├── reasoner.py          # LLM decision making (reads context.memory.*)
│   ├── executor.py          # Tool execution in loop (uses EventEmitter)
│   └── loop.py              # AgentLoop (chat + goal)
│
├── api/                     # Wave 5 — API Gateway
│   ├── __init__.py
│   ├── main.py              # FastAPI app factory
│   ├── auth.py              # Bearer token auth
│   ├── schemas.py           # Request/Response models
│   ├── dependencies.py      # FastAPI Depends injection
│   └── routes/
│       ├── __init__.py
│       ├── admin_auth.py    # /admin/token/init, /admin/tokens
│       ├── chat.py          # /chat, /chat/stream
│       ├── sessions.py      # /sessions CRUD
│       ├── goals.py         # /goals CRUD
│       ├── minions.py       # /admin/minions
│       └── health.py        # /health
│
├── config/                  # Wave 0 — Config System
│   ├── __init__.py
│   ├── models.py            # Pydantic settings
│   └── loader.py            # YAML + env var loader
│
├── db/                      # Wave 1 — DB Pool
│   ├── __init__.py
│   ├── pool.py              # asyncpg connection pool
│   ├── session.py           # DbSession context manager
│   └── migrations/
│       └── runner.py        # SQL migration runner
│
├── events/                  # Wave 1 — Event Bus (The River)
│   ├── __init__.py
│   ├── base.py              # BaseEvent, EventMetadata
│   ├── bus.py               # EventBus (async pub/sub)
│   ├── emitter.py           # EventEmitter — safe publish seam    ← NEW
│   ├── types.py             # Event type constants
│   └── exceptions.py        # EventBusError, etc.
│
├── execution/               # Wave 5 — Execution Module
│   ├── __init__.py
│   └── module.py            # Wraps AgentLoop; goals persisted via GoalRepository
│
├── interaction/             # Wave 5 — Interaction Module
│   ├── __init__.py
│   └── service.py           # InteractionService, PersonalityService
│
├── learning/                # Wave 7.1 — Learning Module (planned)
│   ├── __init__.py          # (planned)
│   ├── models.py            # (planned)
│   ├── interfaces.py        # (planned)
│   ├── encoder.py           # (planned)
│   ├── engine.py            # (planned)
│   ├── readout.py           # (planned)
│   ├── labeler.py           # (planned)
│   ├── persistence.py       # (planned)
│   ├── service.py           # (planned)
│   └── cli.py               # (planned)
│
├── llm/                     # Wave 1 — LLM Client
│   ├── __init__.py
│   ├── base.py              # LLMClient ABC
│   ├── models.py            # ChatMessage, ChatResult, ToolCall
│   ├── config.py            # GenerationConfig
│   ├── factory.py           # LLMClientFactory
│   └── providers/
│       ├── __init__.py
│       └── openai.py        # OpenAIClient
│
├── logging/                 # Wave 0 — Logging
│   ├── __init__.py
│   ├── setup.py             # configure_logging()
│   └── context.py           # trace_id propagation
│
├── memory/                  # Wave 2.4 — Fact Store
│   ├── __init__.py
│   ├── models.py            # Fact, Concept, FactType, FactMutability
│   ├── interfaces.py        # FactRepository, ConceptRepository, FactExtractor ABCs
│   └── repository.py        # PostgresFactRepository + PostgresConceptRepository  ← NEW
│
├── minions/                 # Wave 2.3 — Minion MQTT
│   ├── __init__.py
│   ├── models.py            # MinionInfo, MinionEvent, MinionConfig
│   ├── interfaces.py        # MinionGateway, MinionEventHandler ABCs
│   ├── mqtt_client.py       # MinionMQTTClient
│   ├── registry.py          # InMemoryMinionRegistry
│   └── event_handler.py     # MinionEventProcessor
│
├── services/                # Wave 3 — Service Layer
│   ├── __init__.py
│   ├── memory_service.py    # MemoryService (get_memory_context(), search, store, extract)
│   ├── minion_service.py    # MinionService (MQTT gateway wiring)
│   └── tool_executor.py     # ToolExecutorService (CircuitBreaker + EventEmitter + metrics)
│
├── sessions/                # Wave 2.1 — Session Store
│   ├── __init__.py
│   ├── models.py            # Session, Message, SessionState, MessageRole
│   ├── interfaces.py        # SessionRepository ABC
│   ├── repository.py        # PostgresSessionRepository
│   └── policy.py            # create_session, resume_session, add_user_message, ...  ← NEW (replaces service.py)
│
└── tools/                   # Wave 2.2 — Tool Ecosystem
    ├── __init__.py
    ├── interfaces.py        # Tool, ToolRegistry, ToolExecutor ABCs
    ├── registry.py          # InMemoryToolRegistry
    ├── executor.py          # DefaultToolExecutor
    └── meta/
        ├── __init__.py
        ├── base.py          # MetaTool base
        ├── file_read.py
        ├── file_write.py
        ├── shell.py
        └── grep.py
```

---

## 🧩 File Changes Since Last Zoom-Out

| File | Change |
|------|--------|
| `src/cortex/events/emitter.py` | **🆕 Added** — EventEmitter safe publish seam |
| `src/cortex/events/__init__.py` | **🔄 Updated** — exports EventEmitter |
| `src/cortex/sessions/policy.py` | **🆕 Added** — Session lifecycle policy functions |
| `src/cortex/sessions/service.py` | **🗑️ Removed** — replaced by policy.py |
| `src/cortex/memory/repository.py` | **🆕 Added** — PostgresFactRepository + PostgresConceptRepository |
| `src/cortex/agentic/models.py` | **🔄 Updated** — MemoryContext dataclass; Context.memory field |
| `src/cortex/agentic/context_builder.py` | **🔄 Updated** — uses SessionRepository; calls get_memory_context() |
| `src/cortex/agentic/executor.py` | **🔄 Updated** — uses EventEmitter |
| `src/cortex/main.py` | **🔄 Updated** — session_repository field; no GoalStore |
| `src/cortex/services/memory_service.py` | **🔄 Updated** — get_memory_context() bundle; internalized old public methods |
| `src/cortex/services/tool_executor.py` | **🔄 Updated** — full CircuitBreaker; EventEmitter; ServiceToolExecutor adapter |
| `src/cortex/interaction/service.py` | **🔄 Updated** — SessionRepository + policy instead of SessionService |
| `src/cortex/goals/{interfaces,repository}.py` | **🆕 Added** — GoalRepository (ABC) + Postgres/InMemory implementations |
| `src/cortex/execution/module.py` | **🔄 Updated** — GoalRepository persistence; resume_in_flight startup resume |
| `tests/unit/sessions/test_policy.py` | **🆕 Added** — 7 tests for policy functions |
