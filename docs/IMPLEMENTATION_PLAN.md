# 🏗️ Implementation Plan — Cortex (Bottom-Up Wave)

> Collaborative planning document. Update as decisions are made.
> Created: 2026-04-29

---

## Dependency Graph

```
                          ╔═══════════════════════════════╗
                          ║ ✅ FOUNDATION (Wave 0) ✅   ║
                          ║   Config • Logging • Docker  ║
                          ╚═══════════════════════════════╝
                                              │
                          ╔═══════════════════════════════╗
                          ║      PRIMITIVES (Wave 1)      ║
                          ║  Event Bus • LLM Client • DB  ║
                          ╚═══════════════════════════════╝
                                              │
              ╔═══════════════════════════════════════════════════════╗
              │              WAVE 2: STANDALONE MODULES               ║
              ╠═══════════════╦═══════════════╦═══════════════╦═══════╣
              │  ToolRegistry  │ MinionMQTT   │  FactStore    │Session║
              │  ✅ COMPLETE    │  (pending)   │  (pending)    │ Store ║
              ║  (67 tests)     │              │               │ ✅    ║
              ╚═══════════════╩═══════════════╩═══════════════╩═══════╝
                                              │
              ╔═══════════════════════════════════════════════════════╗
              │              WAVE 3: SERVICE LAYER                    ║
              ╠══════════════════╦══════════════════╦════════════════╣
              │   ToolExecutor    │  MinionService   │  MemoryService   ║
              │   (uses registry) │  (uses mqtt)     │  (uses factstore)║
              ╚══════════════════╩══════════════════╩════════════════╝
                                              │
              ╔═══════════════════════════════════════════════════════╗
              │              WAVE 4: AGENTIC CORE                      ║
              ╠════════════════════════════════════════════════════════╣
              │     ContextBuilder → Reasoner → Executor → Loop        ║
              ╚════════════════════════════════════════════════════════╝
                                              │
              ╔═══════════════════════════════════════════════════════╗
              │              WAVE 5: ORCHESTRATION                     ║
              ╠════════════════════════════════════════════════════════╣
              │   ExecutionModule • InteractionModule • API Gateway     ║
              ╚════════════════════════════════════════════════════════╝
                                              │
              ╠════════════════════════════════════════════════════════╣
              │  ✅ 6.1 cortex-protocol   │ ✅ 6.2 API Gateway │ ✅ 6.3 Bootstrap │ ✅ 6.4 Minions ║
              ╚════════════════════════════════════════════════════════╝
                                              │
              ╔═══════════════════════════════════════════════════════╗
              │              WAVE 7: LEARNING MODULE                   ║
              ╠════════════════════════════════════════════════════════╣
              │  7.1 Reservoir + Salience │ 7.2 Module │ 7.3 Patterns   ║
              ╚════════════════════════════════════════════════════════╝
```

---

## 🌀 Wave 0: Foundation (Infrastructure) ✅ COMPLETE

**Goal:** Establish the bedrock. No dependencies on anything else.

**Status:** Implemented 2026-04-29

### 0.1 Config System ✅

```
src/cortex/config/
├── __init__.py           # exports Settings
├── models.py            # Pydantic settings classes
└── loader.py            # YAML + env var loader with caching
```

**Interface:**

```python
class Settings(BaseSettings):
    """Root settings. All modules import from here."""

    # Database
    database_url: PostgresDsn

    # LLM
    llm_provider: Literal["openai", "anthropic"] = "openai"
    llm_api_key: SecretStr
    llm_model: str = "gpt-4o"

    # MQTT
    mqtt_broker_url: MqttDsn

    # App
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: LogLevel = "INFO"
```

### 0.2 Logging ✅

```
src/cortex/logging/
├── __init__.py           # exports configure_logging, StructuredLogger
├── setup.py              # configure_logging() with console/JSON output
└── context.py            # trace_id propagation, trace_context manager
```

### 0.3 Docker Setup ✅

```
docker-compose.yml        # postgres, mosquitto, cortex
Dockerfile                # Python 3.11 slim
docker/
└── mosquitto.conf        # MQTT broker configuration
```

### 0.4 Postgres Migrations ✅

```
migrations/
├── 001_initial.sql       # schema_migrations table
├── 002_sessions.sql      # sessions, messages
└── 003_facts.sql         # facts, concepts
```

---

## 🌀 Wave 1: Primitives (Event Bus, LLM Client, DB Pool) ✅ COMPLETE

**Goal:** Core abstractions that EVERYTHING else depends on.

**Status:** Implemented 2026-04-29

### 1.1 Event Bus ✅

```
src/cortex/events/
├── __init__.py           # exports EventBus, BaseEvent, EventTypes, Subscription
├── base.py               # BaseEvent, EventMetadata
├── bus.py                # EventBus (async pub/sub implementation)
├── types.py              # EventTypes enum (all event constants)
└── exceptions.py         # EventBusError, EventHandlerError, etc.
```

**Features:**

- Async in-memory pub/sub with wildcard (\*) subscriptions
- Context manager for auto-unsubscribe (`async with bus.subscribed(...)`)
- Error isolation (handler failures don't crash the bus)
- Thread-safe subscription management

**Usage:**

```python
from cortex.events import EventBus, BaseEvent

bus = EventBus()
await bus.start()

async def handle(event: BaseEvent):
    print(f"Received: {event.type}")

await bus.subscribe("user.message", handle)
await bus.publish(BaseEvent.create(
    event_type="user.message",
    payload={"content": "hello"},
    source_module="api"
))
```

### 1.2 LLM Client ✅

```
src/cortex/llm/
├── __init__.py           # exports LLMClient, ChatMessage, ToolCall, etc.
├── base.py               # LLMClient ABC
├── models.py             # ChatMessage, ChatResult, ToolCall, ToolDefinition
├── config.py             # GenerationConfig
├── factory.py            # LLMClientFactory
└── providers/
    ├── __init__.py
    └── openai.py         # OpenAIClient implementation
```

**Features:**

- Provider-agnostic interface (swap OpenAI/Anthropic/etc.)
- Function calling support with JSON schema translation
- Async chat with token usage stats
- Configurable generation parameters

**Usage:**

```python
from cortex.llm import LLMClientFactory

factory = LLMClientFactory(settings)
client = factory.create()  # Creates OpenAI client

result = await client.chat(
    messages=[ChatMessage(role="user", content="Hello!")],
    tools=[ToolDefinition(name="search", description="...", input_schema={...})]
)
```

### 1.3 Database Pool ✅

```
src/cortex/db/
├── __init__.py           # exports create_pool, get_pool, DbSession, run_migrations
├── pool.py               # create_pool(), get_pool(), close_pool()
├── session.py            # DbSession (async context manager)
└── migrations/
    └── runner.py          # run_migrations(), create_migration()
```

**Features:**

- Async connection pool with configurable min/max size
- Simple session context manager for queries
- SQL migration runner with version tracking
- Transaction support

**Usage:**

```python
from cortex.db import create_pool, DbSession, run_migrations

await run_migrations()
await create_pool(settings)

async with DbSession() as session:
    rows = await session.fetch("SELECT * FROM sessions WHERE id = $1", sid)
```

### Tests

35 unit tests covering all Wave 1 components.

---

## 🌀 Wave 2: Standalone Modules (with interfaces)

**Goal:** Each module is fully testable in isolation. Define interfaces FIRST.

### 2.1 Session Store ✅ COMPLETE

```
src/cortex/sessions/
├── __init__.py            # exports Session, SessionService, etc.
├── models.py              # Session, Message, SessionState, MessageRole
├── interfaces.py          # SessionRepository (ABC)
├── repository.py          # PostgresSessionRepository
└── service.py             # SessionService (business logic)
```

**Status:** Implemented 2026-04-30

**Features:**

- Session lifecycle management (created → active → idle → ended)
- Message storage with role tracking (user, assistant, tool_result)
- Tool call serialization in messages
- Conversation history retrieval (newest first, paginated)
- Auto-update of session activity timestamps

**Tests:** 30 tests (models, interface, repository, service)

**Interface:**

```python
class SessionRepository(ABC):
    """Persistence interface for sessions."""

    async def create(self) -> Session: ...
    async def get(self, session_id: UUID) -> Session | None: ...
    async def update_state(self, session_id: UUID, state: SessionState) -> None: ...

    async def add_message(self, session_id: UUID, message: Message) -> Message: ...
    async def get_messages(
        self,
        session_id: UUID,
        limit: int = 50,
        before: datetime | None = None
    ) -> list[Message]: ...
```

**Schema:**

```sql
CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    state VARCHAR(20) NOT NULL DEFAULT 'created',
    created_at TIMESTAMP DEFAULT NOW(),
    last_activity_at TIMESTAMP DEFAULT NOW(),
    ended_at TIMESTAMP,
    metadata JSONB DEFAULT '{}'
);

CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL,  -- user, assistant, system, tool_result
    content TEXT NOT NULL,
    tool_calls JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);
```

### 2.2 Tool Registry ✅ COMPLETE

```
src/cortex/tools/
├── __init__.py
├── interfaces.py          # Tool, ToolExecutor (ABCs), ToolDefinition, ToolResult, ToolCall
├── registry.py            # InMemoryToolRegistry, ToolRegistrar
├── executor.py            # DefaultToolExecutor with timeout, validation, metrics
└── meta/                  # Built-in tools
    ├── __init__.py
    ├── base.py
    ├── file_read.py
    ├── file_write.py
    ├── shell.py
    └── grep.py
```

**Status:** Implemented 2026-04-30

**Features:**

- InMemoryToolRegistry with search, categories, copy
- DefaultToolExecutor with timeout, validation, metrics
- 4 built-in meta tools: file_read, file_write, shell, grep
- Full test coverage (67 tests)

**Tests:** 67 unit tests for interfaces, registry, executor, meta tools

**Interface:**

```python
class Tool(ABC):
    """Base class for all tools."""

    name: str
    description: str
    input_schema: dict  # JSON Schema
    output_schema: dict | None = None
    idempotent: bool = False
    timeout_seconds: int = 60

    @abstractmethod
    async def execute(self, arguments: dict) -> ToolResult:
        """Execute the tool with validated arguments."""
        ...


class ToolRegistry(ABC):
    """Registration and discovery interface."""

    def register(self, tool: Tool) -> None: ...
    def get(self, name: str) -> Tool | None: ...
    def list_all(self) -> list[Tool]: ...
    def get_schemas(self) -> list[ToolDefinition]: ...


class ToolExecutor(ABC):
    """Execution interface."""

    def __init__(self, registry: ToolRegistry): ...

    async def execute(
        self,
        tool_name: str,
        arguments: dict,
        *,
        timeout: int | None = None
    ) -> ToolResult:
        """Execute a tool by name with arguments."""
        ...
```

### 2.3 Minion MQTT Client ✅ COMPLETE

```
src/cortex/minions/
├── __init__.py
├── models.py              # MinionInfo, MinionEvent, MinionEventBatch, MinionConfig
├── interfaces.py          # MinionGateway, MinionEventHandler, MinionRegistry (ABCs)
├── mqtt_client.py         # MinionMQTTClient
├── event_handler.py       # MinionEventProcessor
└── registry.py            # InMemoryMinionRegistry
```

**Status:** Implemented 2026-04-30

**Features:**

- MinionInfo, MinionEvent, MinionEventBatch, MinionConfig models
- MinionGateway ABC for receiving events
- MinionEventHandler ABC for processing events
- InMemoryMinionRegistry for tracking minions
- MinionMQTTClient implementation with async message handling
- 24 tests covering all components

**Tests:** 24 unit tests

```python
class MinionGateway(ABC):
    """Interface for receiving minion events."""

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def subscribe(self, handler: MinionEventHandler) -> None: ...


class MinionEventHandler(Protocol):
    """Handle incoming minion events."""

    async def handle_event(self, event: MinionEvent) -> None: ...
    async def handle_batch(self, batch: MinionEventBatch) -> list[MinionEvent]: ...


class MinionRegistry(ABC):
    """Track registered minions."""

    async def register(self, minion_id: str, info: MinionInfo) -> None: ...
    async def get(self, minion_id: str) -> MinionInfo | None: ...
    async def list_active(self) -> list[MinionInfo]: ...
    async def heartbeat(self, minion_id: str) -> None: ...
```

### 2.4 Fact Store ✅ COMPLETE

```
src/cortex/memory/
├── __init__.py
├── models.py              # Fact, Concept, FactType, FactMutability
└── interfaces.py          # FactRepository, ConceptRepository, FactExtractor (ABCs)
```

**Status:** Implemented 2026-04-30

**Features:**

- Fact model with type, mutability, confidence, symbolic/natural repr
- Concept model for derived knowledge
- FactType enum (location, activity, calendar, etc.)
- FactRepository ABC with full CRUD and search
- ConceptRepository ABC for derived concepts
- FactExtractor ABC for LLM-powered extraction
- 18 tests covering all models and interfaces

**Tests:** 18 unit tests

**Interface:**

```python
class FactRepository(ABC):
    """Persistence interface for facts."""

    async def store(self, fact: Fact) -> Fact: ...
    async def store_batch(self, facts: list[Fact]) -> list[Fact]: ...

    async def get(self, fact_id: UUID) -> Fact | None: ...
    async def retract(self, fact_id: UUID, reason: str | None = None) -> None: ...

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        fact_types: list[FactType] | None = None,
        min_confidence: float | None = None
    ) -> list[Fact]: ...

    async def get_by_type(
        self,
        fact_type: FactType,
        *,
        limit: int = 50
    ) -> list[Fact]: ...

    async def record_access(self, fact_id: UUID) -> None: ...
```

**Schema:**

```sql
CREATE TABLE facts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type VARCHAR(50) NOT NULL,
    mutability VARCHAR(20) NOT NULL DEFAULT 'mutable',
    symbolic_repr TEXT NOT NULL,
    natural_lang_repr TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}',
    confidence FLOAT NOT NULL DEFAULT 0.5,
    layer INTEGER NOT NULL DEFAULT 0,
    access_count INTEGER NOT NULL DEFAULT 0,
    last_accessed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    retracted_at TIMESTAMP
);

CREATE TABLE concepts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbolic_repr TEXT NOT NULL,
    natural_lang_repr TEXT NOT NULL,
    derivation_method VARCHAR(20) NOT NULL,
    proof_chain TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}',
    confidence FLOAT NOT NULL DEFAULT 0.5,
    source_facts UUID[] NOT NULL,
    validated BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    retracted_at TIMESTAMP
);

CREATE INDEX idx_facts_type ON facts(type);
CREATE INDEX idx_facts_layer ON facts(layer);
CREATE INDEX idx_facts_access ON facts(access_count DESC);
CREATE INDEX idx_facts_search ON USING gin(to_jsonb(facts) gin_btree_ops);
```

---

## 🌀 Wave 3: Service Layer ✅ DONE

**Goal:** Modules that USE Wave 2 interfaces. Fully wired but not yet orchestrated.

### 3.1 Tool Executor Service

```
src/cortex/services/tool_executor.py
```

**Implementation:**

```python
class ToolExecutorService(ToolExecutor):
    """Full implementation wrapping registry."""

    def __init__(
        self,
        registry: ToolRegistry,
        event_bus: EventBus,
        circuit_breaker: CircuitBreaker
    ): ...

    async def execute(
        self,
        tool_name: str,
        arguments: dict,
        *,
        timeout: int | None = None
    ) -> ToolResult:
        # Validate tool exists
        # Wrap with circuit breaker
        # Execute
        # Emit tool.result event
        # Return result
```

### 3.2 Minion Service

```
src/cortex/services/minion_service.py
```

**Implementation:**

```python
class MinionService(MinionGateway):
    """Full implementation wrapping MQTT client."""

    def __init__(
        self,
        config: MinionConfig,
        event_bus: EventBus,
        registry: MinionRegistry,
        handler: MinionEventHandler
    ): ...

    async def connect(self) -> None:
        # Setup MQTT client
        # Configure auth
        # Start listening
```

### 3.3 Memory Service

```
src/cortex/services/memory_service.py
```

**Implementation:**

```python
class MemoryService:
    """Service API for the Memory Module."""

    def __init__(
        self,
        repository: FactRepository,
        llm_client: LLMClient,
        event_bus: EventBus
    ): ...

    # ─── Query Methods (for Agentic Loop) ───

    async def get_relevant(
        self,
        query: str,
        *,
        limit: int = 10,
        session_id: UUID | None = None,
        fact_types: list[FactType] | None = None
    ) -> list[Fact]:
        """
        Get facts relevant to a query.

        Strategy:
        1. Semantic search (embeddings)
        2. Boost facts from current session
        3. Boost recent facts (recency)
        4. Boost high-confidence facts
        """
        ...

    async def get_context(
        self,
        dimensions: list[str] = ["time", "location", "activity"]
    ) -> dict[str, Any]:
        """Get current ambient context."""
        ...

    async def get_personality_context(
        self,
        session_id: UUID | None = None
    ) -> PersonalityContext:
        """Get personality traits for response formatting."""
        ...

    # ─── Storage Methods (internal) ───

    async def store_fact(self, fact: Fact) -> Fact:
        """Store a new fact. Handles deduplication."""
        ...

    async def retract_fact(self, fact_id: UUID, reason: str | None = None) -> None:
        """Retract a fact. Cascade invalidate derived concepts."""
        ...

    # ─── Fact Extraction (from events) ───

    async def handle_event(self, event: BaseEvent) -> None:
        """Process incoming events, extract facts."""
        match event.type:
            case "user.message":
                await self._extract_from_conversation(event)
            case "location":
                await self._extract_location_facts(event)
            case "payment":
                await self._extract_payment_facts(event)
            # etc.
```

---

**Status:** Implemented 2026-04-30

**Tests:** 79 unit tests covering all agentic components.

## 🌀 Wave 5: Orchestration ✅ DONE

**Status:** Implemented 2026-04-30

**Tests:** 39 unit tests covering execution and interaction modules.

### 5.1 ExecutionModule ✅

Wraps the AgentLoop with goal lifecycle management.

### 5.2 InteractionModule ✅

Thin interface for receiving requests and formatting responses.

```
src/cortex/agentic/
├── __init__.py
├── models.py              # Context, Decision, Mode, etc.
├── context_builder.py    # Assembles reasoning context
├── reasoner.py           # LLM decision making
├── executor.py           # Tool execution (wraps ToolExecutorService)
├── conversation.py        # Context window management
└── loop.py               # Main AgentLoop
```

### 4.1 Context Builder

```python
@dataclass
class Context:
    """All context needed for LLM reasoning."""
    conversation: list[Message]
    facts: list[Fact]
    tools: list[ToolDefinition]
    personality: PersonalityContext
    goal: GoalContext | None
    ambient: AmbientContext  # time, location, activity


class ContextBuilder:
    """Assembles context from all sources."""

    def __init__(
        self,
        session_service: SessionService,
        memory_service: MemoryService,
        tool_registry: ToolRegistry,
        personality_service: PersonalityService
    ): ...

    async def build(
        self,
        session_id: UUID,
        user_message: str,
        mode: Mode,
        goal_id: UUID | None = None
    ) -> Context:
        """
        Build complete reasoning context.

        1. Conversation history (last 20 messages)
        2. Relevant facts from Memory
        3. Available tool schemas
        4. Personality context
        5. Goal context (if mode=GOAL)
        6. Ambient context (time, location)
        """
        ...
```

### 4.2 Reasoner

```python
class Reasoner:
    """LLM-powered decision making."""

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        system_prompt: str
    ): ...

    async def reason(
        self,
        context: Context
    ) -> Decision:
        """
        Given context, decide what to do next.

        Returns:
        - Decision.respond(text): Done, return to user
        - Decision.execute_tools(calls): Execute tools, continue
        - Decision.ask_question(question): Need clarification
        """
        ...

    def _build_prompt(self, context: Context) -> list[ChatMessage]:
        """Build system prompt + user message."""
        ...
```

### 4.3 Executor

```python
class LoopExecutor:
    """Tool execution within the loop."""

    def __init__(
        self,
        tool_executor: ToolExecutorService,
        event_bus: EventBus
    ): ...

    async def execute_tools(
        self,
        tool_calls: list[ToolCall]
    ) -> list[ToolResult]:
        """
        Execute tool calls.

        Strategy: Sequential for dependent, parallel for independent.
        """
        ...

    async def execute_single(
        self,
        tool_call: ToolCall
    ) -> ToolResult:
        ...
```

### 4.4 Main Loop

```python
class AgentLoop:
    """
    Core agentic loop: Think → Act → Observe → Respond

    Two modes:
    - CHAT: Interactive conversation
    - GOAL: Background task execution
    """

    def __init__(
        self,
        context_builder: ContextBuilder,
        reasoner: Reasoner,
        executor: LoopExecutor,
        event_bus: EventBus
    ): ...

    async def run_chat(
        self,
        session_id: UUID,
        user_message: str
    ) -> ChatResponse:
        """
        Run loop for chat mode.

        Loop: Context → Think → [Act → Observe] → Respond

        Safety: Max 20 iterations, then raise MaxIterationsError.
        """
        messages = [Message(role=Role.USER, content=user_message)]
        iterations = 0

        while iterations < self.max_iterations:
            # 1. Context
            context = await self.context_builder.build(
                session_id=session_id,
                user_message=user_message,
                mode=Mode.CHAT
            )

            # 2. Think
            decision = await self.reasoner.reason(context)

            match decision:
                case Decision.respond(text):
                    return ChatResponse(message=text, iterations=iterations)

                case Decision.execute_tools(calls):
                    # 3. Act
                    results = await self.executor.execute_tools(calls)
                    # 4. Observe
                    messages.extend(self._tool_messages(calls, results))
                    iterations += 1

                case Decision.ask_question(q):
                    return ChatResponse(message=q, iterations=iterations)

        raise MaxIterationsError(self.max_iterations)

    async def run_goal(
        self,
        goal_id: UUID,
        description: str
    ) -> GoalResult:
        """
        Run loop for goal mode.

        Longer-running, emits goal.status events.
        Max 100 iterations.
        """
        ...
```

---

## 🌀 Wave 5: Orchestration ✅ DONE

### 5.1 ExecutionModule ✅

```
src/cortex/execution/
├── __init__.py
└── module.py             # ExecutionModule, GoalStore
```

**ExecutionModule** wraps the AgentLoop and handles goal lifecycle:

```python
class ExecutionModule:
    """
    Execution Module wraps the AgentLoop.

    Subscribes to:
    - goal.created
    - recommendation.executed

    Emits:
    - goal.status
    - goal.completed
    - goal.failed
    """

    def __init__(
        self,
        agent_loop: AgentLoop,
        goal_store: GoalStore,
        event_bus: EventBus
    ): ...

    async def create_goal(
        self,
        description: str,
        priority: str = "normal",
        deadline: datetime | None = None
    ) -> Goal:
        """Create a goal and start execution."""
        goal = await self.goal_store.create(description, priority, deadline)
        asyncio.create_task(self.agent_loop.run_goal(goal.id, description))
        return goal

    async def handle_event(self, event: BaseEvent) -> None:
        """Handle subscribed events."""
        ...
```

### 5.2 InteractionModule ✅

```
src/cortex/interaction/
├── __init__.py
└── service.py            # InteractionService, PersonalityService
```

**InteractionModule** is a "thin interface":

```python
class InteractionService:
    """
    Thin interface: receives requests, calls Agentic Loop, formats responses.

    Does NOT contain the loop itself.
    """

    def __init__(
        self,
        execution_module: ExecutionModule,
        session_service: SessionService,
        personality_service: PersonalityService
    ): ...

    async def handle_message(
        self,
        session_id: UUID | None,
        content: str,
        mode: Mode = Mode.CHAT
    ) -> ChatResponse:
        """Handle incoming user message."""
        # Create/resume session
        session = await self._get_or_create_session(session_id)

        # Call execution module
        if mode == Mode.CHAT:
            response = await self.execution_module.agent_loop.run_chat(
                session.id, content
            )
        else:
            response = await self.execution_module.handle_goal(content)

        # Add to conversation
        await self.session_service.add_message(session.id, Message(
            role=Role.USER,
            content=content
        ))
        await self.session_service.add_message(session.id, Message(
            role=Role.ASSISTANT,
            content=response.message
        ))

        return response
```

### 5.3 API Gateway ✅

```
src/cortex/api/
├── __init__.py
├── main.py               # FastAPI app factory + bootstrap_app()
├── auth.py               # Token generation, Bearer middleware, SHA-256 hashing
├── schemas.py            # Request/Response Pydantic models
├── dependencies.py        # FastAPI Depends injection (set_app_state)
├── cli.py                 # CLI: cortex token:create
└── routes/
    ├── __init__.py
    ├── admin_auth.py     # /admin/token/init, /admin/tokens (auth)
    ├── chat.py           # /chat, /chat/stream (auth)
    ├── sessions.py       # /sessions CRUD (auth)
    ├── goals.py          # /goals CRUD (auth)
    ├── minions.py        # /admin/minions (auth)
    └── health.py         # /health (open)
```

**Status:** Implemented 2026-05-05 (commit 05caad0)

**Routes:**
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | /health | open | DB, MQTT, LLM health |
| POST | /admin/token/init | open | Bootstrap first token |
| POST | /admin/tokens | auth | Create token |
| GET | /admin/tokens | auth | List tokens |
| GET | /admin/minions | auth | List minions |
| GET | /admin/minions/{id} | auth | Minion details |
| POST | /admin/minions/{id}/token | auth | Generate MQTT password |
| DELETE | /admin/minions/{id}/token | auth | Revoke token |
| POST | /admin/minions/{id}/config | auth | Push config via MQTT |
| POST | /chat | auth | Non-streaming chat |
| POST | /chat/stream | auth | SSE streaming |
| GET | /sessions | auth | List sessions |
| POST | /sessions | auth | Create session |
| GET | /sessions/{id} | auth | Get with messages |
| POST | /sessions/{id}/messages | auth | Add message |
| POST | /sessions/{id}/end | auth | End session |
| POST | /sessions/{id}/resume | auth | Resume idle session |
| POST | /goals | auth | Create + run goal |
| GET | /goals | auth | List active goals |
| GET | /goals/{id} | auth | Get goal status |

**CLI:** `cortex token:create <name> [--db-url URL]`

**Tests:** 11 unit tests (test_auth.py)

**Migration:** 005_api_and_minions.sql (api_keys, minions, goals tables)

---

## 🌀 Wave 6: Integration & Minions

### 6.1 cortex-protocol Package ✅ COMPLETE (2026-05-03)

**Package location:** `src/cortex_protocol/`
**Install:** `pip install -e src/cortex_protocol/`

**12 event types:** LocationEvent, ActivityEvent, CalendarEvent, AppUsageEvent, CallLogEvent, PaymentEvent, RefundEvent, ScreenActivityEvent, ApplicationFocusEvent, KeyboardActivityEvent, BatteryEvent, NetworkStatusEvent

**MQTT topics:** MQTTTopics.events/heartbeat/register/commands/command_config
**JSON Schema export:** `from cortex_protocol.schemas.jsonschema import export_to_directory`

**Tests:** 38 unit tests

---

### 6.2 API Gateway ✅ COMPLETE (2026-05-05)

See [Wave 5: Orchestration > 5.3 API Gateway](#53-api-gateway-) section above.

---

### 6.3 Application Bootstrap ✅ COMPLETE (2026-05-03)

```
src/cortex/
├── __init__.py
├── app.py                # CortexApp (wires everything)
├── main.py               # entry point
└── shutdown.py           # graceful shutdown
```

```python
async def create_app() -> CortexApp:
    """Wire up all services and modules."""
    # 1. Config
    settings = get_settings()

    # 2. DB
    await run_migrations()
    db_pool = await create_pool(settings.database_url)

    # 3. Event Bus
    event_bus = EventBus()

    # 4. Core services
    session_repo = PostgresSessionRepository(db_pool)
    session_service = SessionService(session_repo)

    fact_repo = PostgresFactRepository(db_pool)
    memory_service = MemoryService(fact_repo, llm_client, event_bus)

    # 5. Tool ecosystem
    tool_registry = ToolRegistry()
    tool_executor = ToolExecutorService(tool_registry, event_bus, ...)
    register_meta_tools(tool_registry)

    # 6. Minion service
    minion_registry = MinionRegistry(db_pool)
    minion_service = MinionService(config, event_bus, minion_registry, memory_service)

    # 7. Agentic core
    personality_service = PersonalityService(memory_service)
    context_builder = ContextBuilder(session_service, memory_service, tool_registry, personality_service)
    reasoner = Reasoner(llm_client, tool_registry, system_prompt)
    executor = LoopExecutor(tool_executor, event_bus)
    agent_loop = AgentLoop(context_builder, reasoner, executor, event_bus)

    # 8. Orchestration
    goal_store = GoalStore(db_pool)
    execution_module = ExecutionModule(agent_loop, goal_store, event_bus)
    interaction_service = InteractionService(execution_module, session_service, personality_service)

    # 9. Subscribe to events
    memory_service.subscribe(event_bus)
    execution_module.subscribe(event_bus)

    # 10. Start services
    await event_bus.start()
    await minion_service.connect()

    return CortexApp(
        settings=settings,
        event_bus=event_bus,
        interaction_service=interaction_service,
        execution_module=execution_module,
        minion_service=minion_service,
        # etc.
    )
```

### 6.4 Laptop Minion ✅ COMPLETE (2026-05-12)

```
src/minion/
├── __init__.py
├── main.py               # CLI entry point
├── config.py             # MinionConfig
├── mqtt_client.py        # MQTT publisher
├── sensors/
│   ├── __init__.py
│   ├── location.py       # GPS sensor
│   ├── calendar.py       # Calendar sensor
│   ├── activity.py       # Activity recognition
│   └── app_usage.py      # App usage
├── collectors/
│   ├── __init__.py
│   └── batch.py          # Batch events
└── cli.py                # CLI interface
```

---

## 🌀 Wave 7: Learning Module

**Goal:** Detect patterns and salience in continuous minion event streams cheaply enough to score every event without LLM cost. Three components ship across this wave; **7.1 establishes the engine and a single end-to-end vertical slice (salience scoring)**.

**Status:** Planned 2026-05-05

> Architectural rationale: see [`adr/0001-reservoir-computing-for-learning-module.md`](./adr/0001-reservoir-computing-for-learning-module.md).
> Original proposal: [`FEATURES_TO_ADD.md`](./FEATURES_TO_ADD.md).

### Dependencies

- **Hard:** Wave 1 (EventBus, DB) ✅, Wave 2.3 (MinionMQTT) ✅, Wave 2.4 (FactStore) ✅, Wave 3 (MinionService, MemoryService) ✅
- **Soft:** Wave 6.3 (Phone Minion) — improves training data quality once live data flows; **not** a blocker. 7.1 is developed in parallel against synthetic event streams.
- **New runtime dependency:** `numpy` (no other ML libraries).

### 7.1 ReservoirEngine + Salience Readout

**Scope:** End-to-end vertical slice — encoder + reservoir + a single salience readout + offline batch training + persistence + CLI.

**Non-goals (deferred to 7.2 / 7.3):**

- ❌ Event-bus subscription / `LearningModule` class
- ❌ Attaching salience to in-flight `EventMetadata.salience`
- ❌ Anomaly readout
- ❌ Pattern catalogue and `pattern.detected` events
- ❌ Online / RLS training
- ❌ Free-text string features (`merchant_name`, `app_name`, …)
- ❌ Per-user lat/lon anchor learning

**Package layout:**

```
src/cortex/learning/
├── __init__.py
├── models.py          # FeatureVector, ReadoutVersion, TrainingSample, ReservoirConfig
├── interfaces.py      # ReservoirEngine, ReadoutModel, ReadoutRepository (ABCs)
├── encoder.py         # MinionEventEncoder: MinionEvent → np.ndarray, INPUT_DIM = 64
├── engine.py          # ESNReservoirEngine — pure-NumPy ESN, deterministic from seed
├── readout.py         # RidgeReadout — closed-form weights = (XᵀX + λI)⁻¹ XᵀY
├── labeler.py         # SalienceLabeler — heuristic 0.5·rarity + 0.3·hour_anomaly + 0.2·recency
├── persistence.py     # PostgresReadoutRepository — bytea round-trip via np.save / np.load
├── service.py         # LearningService — stateful inference + offline batch training
└── cli.py             # python -m cortex.learning.cli train-salience [...]
```

**Encoder contract (fixed for 7.1; changing it requires retraining all readouts):**

```
INPUT_DIM = 64

[0:12]  type one-hot (12 minion event types)
[12:16] time features: sin/cos(2π · hour/24), sin/cos(2π · weekday/7)
[16:64] type-specific dense features, zero-padded:
        - numeric fields: amounts log-scaled, durations log-scaled, lat/lon centred, etc.
        - low-cardinality enums (ActivityType, MerchantCategory, AppCategory,
          NetworkType, TransactionType, UsageType) one-hot inline
        - free-text strings dropped in 7.1 (deferred to 7.3)
```

**Service shape (stateful, single-writer):**

```python
class LearningService:
    async def warmup(self, n_events: int = 256) -> None: ...

    # Inference (hot path) — mutates live x_t
    async def score_salience(self, event: MinionEvent) -> float: ...

    # Training (cold path) — uses a *throwaway* reservoir; does not touch live state
    async def train_salience_readout(
        self,
        window_days: int = 30,
        ridge_lambda: float = 1e-3,
    ) -> ReadoutVersion: ...

    async def get_active_readout(self, target: str) -> ReadoutVersion: ...
```

- Live reservoir state `x_t` is in-memory only. One `asyncio.Lock` around mutation.
- On startup, `warmup()` replays the last N events from `MinionService` to settle dynamics (~10–50 events suffice with leak rate α = 0.3).
- Training builds a freshly-seeded throwaway reservoir to compute historical `(X, Y)`; the live reservoir is never disturbed.
- App Bootstrap (Wave 6.2) is responsible for ordering: `MinionService` ready → `LearningService.warmup()` → service available.

**Reservoir defaults (replaceable hyperparameters):**

| Parameter         | Default | Notes                                                   |
| ----------------- | ------- | ------------------------------------------------------- |
| `reservoir_dim`   | 500     | small enough for one container, big enough for 12-modal input |
| `spectral_radius` | 0.9     | edge-of-chaos default                                   |
| `leak_rate` (α)   | 0.3     | ~10–50 events of warmup                                 |
| `sparsity`        | 0.1     | 10% non-zero entries in `W_res`                         |
| `input_scaling`   | 1.0     |                                                         |
| `seed`            | 42      | matrices regenerated deterministically from seed        |
| `ridge_lambda`    | 1e-3    | salience training default                               |

**Salience label heuristic (fixed for 7.1):**

```
salience(event) = 0.5 · type_rarity + 0.3 · hour_anomaly + 0.2 · recency_anomaly

  type_rarity     = 1 - clamp01(count(this.type, last_30d) / max_count_any_type)
  hour_anomaly    = 1 - P(this.type | this.hour_of_day, last_30d)
  recency_anomaly = clamp01( log1p(seconds_since_last_same_type) / log1p(7 · 86400) )
```

The 7.1 readout learns to approximate this heuristic in reservoir-state space. Richer labels (LLM-graded, user-feedback) plug in by *replacing* `SalienceLabeler` and re-training; reservoir, encoder, schema, and service API are unchanged.

**Migration:**

```sql
-- migrations/00X_learning.sql

CREATE TABLE reservoir_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seed BIGINT NOT NULL,
    reservoir_dim INT NOT NULL,
    input_dim INT NOT NULL,
    spectral_radius REAL NOT NULL,
    leak_rate REAL NOT NULL,
    sparsity REAL NOT NULL,
    input_scaling REAL NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE readout_models (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    config_id UUID NOT NULL REFERENCES reservoir_configs(id),
    target VARCHAR(32) NOT NULL,            -- 'salience' for 7.1; 'anomaly', 'pattern.<name>' later
    weights BYTEA NOT NULL,                 -- np.save format, ~reservoir_dim floats
    bias REAL NOT NULL,
    ridge_lambda REAL NOT NULL,
    training_samples INT NOT NULL,
    train_rmse REAL,
    trained_at TIMESTAMP DEFAULT NOW(),
    active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX idx_readout_active ON readout_models(target, active) WHERE active;
```

Reservoir matrices `W_res`, `W_in` are **not** stored — regenerated deterministically from `seed + reservoir_dim + input_dim + sparsity` on startup.

**Definition of Done:**

- [ ] Package layout above implemented
- [ ] Migration applied: `reservoir_configs` + `readout_models` tables
- [ ] CLI: `python -m cortex.learning.cli train-salience [--window-days 30] [--ridge-lambda 1e-3]`
      pulls events from `MinionService` → encoder → fresh seeded reservoir → `SalienceLabeler` →
      ridge fit → inserts a new active `readout_models` row, deactivates prior
- [ ] Unit tests (~40–50):
  - encoder: per-type output shape == 64, type one-hot correctness, time sin/cos identity
  - engine: state update converges to fixed point on constant input; spectral radius respected
  - readout: ridge regression recovers known weights on synthetic linearly-separable data
  - labeler: each component bounded in [0,1]; deterministic on fixed events
  - service.train_salience_readout: end-to-end happy path with in-memory minion stub
  - service.score_salience: mutates state; multiple calls produce different `x_t`
  - persistence: round-trip readout weights through `bytea`
- [ ] Integration test (1): CLI invoked against docker-compose Postgres + seeded minion events
      produces an active `readout_models` row; `score_salience` returns values in [0, 1]

### 7.2 LearningModule + Anomaly Readout (deferred)

**Scope:** Subscribe to `*` on the event bus. Score every minion event's salience and attach to `EventMetadata.salience`. Add an anomaly readout. Decide attachment mechanism (publisher-side middleware in `events/bus.py` vs `event.scored` correlation event).

### 7.3 Pattern Catalogue + `pattern.detected` (deferred)

**Scope:** Named pattern readouts (commute, dining, work-from-home, …). Per-user lat/lon anchor learning. Free-text string features via hashing trick or learned embeddings.

---

## 📋 Wave Summary Table

| Wave    | Modules       | Deliverables                                      | Status                 |
| ------- | ------------- | ------------------------------------------------- | ---------------------- |
| **0**   | Foundation    | Config, Logging, Docker                           | ✅ Complete            |
| **1**   | Primitives    | EventBus, LLMClient, DB                           | ✅ Complete            |
| **2.1** | Session Store | SessionRepo, SessionService                       | ✅ Complete            |
| **2.2** | Tool Registry | Tool, ToolRegistry, Executor, Meta Tools          | ✅ Complete            |
| **2.3** | MinionMQTT    | MinionGateway, EventHandler                       | ✅ DONE                |
| **2.4** | Fact Store    | FactRepository, FactStore                         | ✅ DONE                |
| **3**   | Services      | ToolExecutorService, MinionService, MemoryService | ✅ DONE                |
| **4**   | Agentic       | ContextBuilder, Reasoner, Executor, Loop          | ✅ DONE (79 tests)     |
| **5**   | Orchestration | ExecutionModule, InteractionModule, API Gateway    | ✅ DONE (386 tests)    |
| **6.1** | Integration   | cortex-protocol                                   | ✅ DONE (38 tests)     |
| **6.2** | Integration   | API Gateway                                        | ✅ DONE (11 tests)     |
| **6.3** | Integration   | Application Bootstrap (CortexApp wiring)           | ✅ DONE                |
| **6.4** | Integration   | Laptop Minion                                      | ✅ DONE                |
| **7.1** | Learning      | ReservoirEngine + Salience Readout + CLI          | 📋 Planned             |

---

## 🎯 Implementation Order

```
Week 1-2:  Wave 0 (Foundation) ✅ DONE
           └── Config, logging, docker-compose, migrations

Week 3-4:  Wave 1 (Primitives) ✅ DONE
           └── TDD: event pub/sub, LLM chat, DB pool

Week 5-6:  Wave 2.1 (Session Store) ✅ DONE
           └── TDD: session CRUD, message storage, service layer
           └── 30 tests added

Week 6-7:  Wave 2.2 (Tool Registry)
           ├── TDD: tool registry + executor
           ├── Built-in tools: file_read, file_write, shell, grep
           └── TDD: tool execution, error handling

Week 7-8:  Wave 2.4 (Fact Store)
           ├── TDD: fact CRUD, search
           └── LLM-powered fact extraction

Week 8-9:  Wave 2.3 (MinionMQTT)
           ├── TDD: minion event handling
           └── MQTT client implementation

Week 10-12: Wave 3-4 (Services + Agentic Core)
           ├── Wire: Service layer
           ├── Implement: Agentic Loop
           └── E2E: Chat flow with tools

Week 13-14: Wave 5 (Orchestration) ✅ DONE + Wave 6 (Integration) ✅ DONE
           ├── Implement: API Gateway ✅
           ├── Wire: Full application bootstrap
           └── Implement: PhoneMinion

Week 15-16: Wave 7.1 (Learning — ReservoirEngine + Salience)
           ├── TDD: encoder, ESN engine, ridge readout, labeler
           ├── Migration: reservoir_configs, readout_models
           └── CLI: `cortex.learning train-salience` + integration test
```

---

## 📁 Current File Structure

```
cortex/
├── src/
│   └── cortex/
│       ├── __init__.py
│       ├── main.py
│       ├── config/           # Wave 0 ✅
│       │   ├── __init__.py
│       │   ├── models.py
│       │   └── loader.py
│       ├── logging/          # Wave 0 ✅
│       │   ├── __init__.py
│       │   ├── setup.py
│       │   └── context.py
│       ├── events/           # Wave 1 ✅
│       │   ├── __init__.py
│       │   ├── base.py
│       │   ├── bus.py
│       │   ├── types.py
│       │   └── exceptions.py
│       ├── llm/              # Wave 1 ✅
│       │   ├── __init__.py
│       │   ├── base.py
│       │   ├── models.py
│       │   ├── config.py
│       │   ├── factory.py
│       │   └── providers/
│       │       ├── __init__.py
│       │       └── openai.py
│       └── db/               # Wave 1 ✅
│           ├── __init__.py
│           ├── pool.py
│           ├── session.py
│           └── migrations/
│               └── runner.py
│
├── cortex_protocol/         # Wave 6.1 ✅ (38 tests)
├── sessions/                # Wave 2.1 ✅
│   ├── __init__.py
│   ├── models.py
│   ├── interfaces.py
│   ├── repository.py
│   └── service.py
│
├── tools/                   # Wave 2.2 ✅
│   ├── __init__.py
│   ├── interfaces.py
│   ├── registry.py
│   ├── executor.py
│   └── meta/
│       ├── __init__.py
│       ├── base.py
│       ├── file_read.py
│       ├── file_write.py
│       ├── shell.py
│       └── grep.py
│
├── minions/                 # Wave 2.3 ✅
│   ├── __init__.py
│   ├── models.py
│   ├── interfaces.py
│   ├── registry.py
│   ├── mqtt_client.py
│   └── event_handler.py
│
├── memory/                  # Wave 2.4 ✅
│   ├── __init__.py
│   ├── models.py
│   └── interfaces.py
│
├── agentic/                  # Wave 4 ✅ (79 tests)
│   ├── __init__.py
│   ├── models.py
│   ├── context_builder.py
│   ├── reasoner.py
│   ├── executor.py
│   └── loop.py
│
├── execution/                # Wave 5 ✅ (18 tests)
│   ├── __init__.py
│   └── module.py
│
├── interaction/              # Wave 5 ✅ (21 tests)
│   ├── __init__.py
│   └── service.py
│
├── learning/                 # Wave 7.1 📋 (planned)
│   ├── __init__.py
│   ├── models.py
│   ├── interfaces.py
│   ├── encoder.py
│   ├── engine.py
│   ├── readout.py
│   ├── labeler.py
│   ├── persistence.py
│   ├── service.py
│   └── cli.py
│
├── migrations/               # Wave 0 ✅
├── docker/                   # Wave 0 ✅
├── tests/
│   └── unit/                 # 386 tests (all waves)
├── docker-compose.yml        # Wave 0 ✅
├── Dockerfile                # Wave 0 ✅
├── pyproject.toml           # Wave 0 ✅
├── config.yaml              # Wave 0 ✅
└── doc/
    ├── ARCHITECTURE.md
    ├── AGENTIC_LOOP.md
    ├── MINION_PROTOCOL.md
    ├── MINION_EVENTS.md
    └── IMPLEMENTATION_PLAN.md
```

---

## Key Decisions

| Decision            | Options                               | Recommendation                        |
| ------------------- | ------------------------------------- | ------------------------------------- |
| **Web framework**   | FastAPI vs Flask vs Litestar          | **FastAPI** (async native, auto-docs) |
| **DB ORM**          | SQLAlchemy vs raw psycopg vs Pydantic | **asyncpg** (async, no ORM overhead)  |
| **MQTT library**    | mqttio vs aiomqtt vs hbmqtt           | **aiomqtt** (active, simple)          |
| **LLM provider**    | OpenAI only vs multiple               | OpenAI only for v1                    |
| **Minion language** | Python vs Go vs Rust                  | Python (shares code with Cortex)      |
| **Session storage** | Postgres vs Redis                     | Postgres (already in stack)           |
| **Config format**   | YAML only vs JSON vs TOML             | YAML + env vars                       |
| **Learning ML stack** | LLM-only vs sklearn vs reservoir computing | **Reservoir computing (NumPy)** — see [ADR-0001](./adr/0001-reservoir-computing-for-learning-module.md) |
| **Reservoir library** | reservoirpy vs scratch NumPy         | **NumPy** (~80 LoC, no transitive deps) |
| **Readout training** | Online RLS vs offline batch ridge     | **Offline batch ridge** (closed form) |

---

## Success Criteria

After Wave 6:

### Core Agentic Loop (Wave 4)

- [x] Agentic Loop executes: Context → Think → Act → Respond cycle
- [x] LLM can reason and decide to use tools
- [x] Tools execute and results feed back into loop
- [x] Loop terminates with text response (or max iterations)
- [x] Context window managed (long conversations truncated)

### Chat Mode (Wave 5)

- [x] `POST /chat` returns a response from LLM
- [x] Tools can be executed via chat ("read file X")
- [x] Multi-tool conversations work (tool → result → tool → response)
- [x] Conversation history available across messages

### Goal Mode (Wave 5)

- [x] `POST /goals` creates and runs a goal
- [x] Long-running goals can be paused/resumed
- [x] Goal progress tracked and events emitted

### Full System (Wave 6)

- [x] Minion location events flow to Cortex
- [x] Facts are extracted and stored in Postgres
- [x] Conversation history persists across sessions
- [x] Docker compose brings up entire stack
- [x] Health endpoint returns healthy status

### Learning Module — 7.1 (Wave 7)

- [ ] `python -m cortex.learning.cli train-salience` produces an active `readout_models` row
- [ ] `LearningService.score_salience(event)` returns a value in [0, 1] from the trained readout
- [ ] Reservoir warmup completes for 256 events on startup without blocking the event loop
- [ ] `reservoir_configs` and `readout_models` tables present after migration

---

_Last updated: 2026-05-12_ (Wave 0-6 ✅, Wave 7.1 planned, 394 tests)
