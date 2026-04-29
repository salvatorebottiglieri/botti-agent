# Implementation Plan — botti-agent / Cortex

> Bottom-up implementation divided into waves. Each wave builds on the previous.
> Created: 2026-04-29

---

## Dependency Analysis

Before diving into waves, here's the dependency graph:

```
                    ┌─────────────┐
                    │   Config    │
                    │  (global)   │
                    └──────┬──────┘
                           │
         ┌─────────────────┼─────────────────┐
         │                 │                 │
         ▼                 ▼                 ▼
    ┌─────────┐       ┌──────────┐       ┌──────────┐
    │ Postgres│       │Event Bus │       │ Sessions │
    │ Schema  │       │  (base)  │       │  Store   │
    └────┬────┘       └────┬─────┘       └────┬─────┘
         │                 │                 │
         └────────┬────────┴─────────────────┘
                  │        FOUNDATION (Wave 0)
                  ▼
         ┌───────────────┐
         │ LLM Abstraction│
         │    (base)     │
         └───────┬───────┘
                 │
    ┌────────────┼────────────┐
    │            │            │
    ▼            ▼            ▼
┌────────┐  ┌──────────┐  ┌─────────────┐
│  Tool  │  │ Minion   │  │  Memory     │
│Ecosystem│  │ Protocol │  │  Module     │
└────────┘  └──────────┘  └─────────────┘
                                      │
                                      │ WAVE 2 (independent)
                                      │
                    ┌─────────────────┼─────────────────┐
                    │                 │                 │
                    ▼                 ▼                 ▼
              ┌───────────┐    ┌─────────────┐    ┌─────────────┐
              │  Agentic  │    │  Learning   │    │Interaction  │
              │   Loop    │    │  Module     │    │   Module    │
              │  (core)   │    │             │    │  (thin)     │
              └─────┬─────┘    └─────────────┘    └─────────────┘
                    │
                    │ WAVE 3
                    │
                    ▼
              ┌─────────────┐
              │  Execution  │◄──── Depends on Agentic Loop
              │   Module    │
              └─────┬───────┘
                    │
                    │ WAVE 4
                    ▼
              ┌─────────────┐
              │ API Gateway │
              └─────┬───────┘
                    │
                    │ WAVE 5
                    ▼
              ┌─────────────────┐
              │ Full Integration│
              └─────────────────┘
```

**Key insight:** The **Agentic Loop** is the core component that ties everything together. It depends on:
- LLM Abstraction (for reasoning)
- Memory Service (for context)
- Tool Ecosystem (for acting)

The **Execution Module** uses the Agentic Loop to handle both chat and goals.

---

## Wave Summary

| Wave | Components | Key Deliverables |
|------|-----------|------------------|
| **0** | Infrastructure | Config, Postgres, Docker, Logging |
| **1** | Abstractions | Event Bus, LLM Client, Sessions |
| **2** | Independent Modules | Tool Ecosystem, Minion Protocol, Memory Service |
| **3** | **Agentic Loop** | Context Builder, Reasoner, Executor, Conversation Manager |
| **4** | Orchestration | Execution Module (Agentic Loop), API Gateway |
| **5** | Integration | Full system, Minion implementations |

---

## Wave 0: Infrastructure Foundation

> No dependencies. Sets up everything else.

### Goals
- Development environment ready
- Database schema in place
- Docker containers defined
- Logging configured

### Deliverables

#### 0.1 Config System
```
src/cortex/config.py
```
- Pydantic Settings for all config
- YAML file loading + env var overrides
- No runtime reload (restart required)

```python
# src/cortex/config.py
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Database
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "cortex"
    postgres_password: str = ""
    postgres_db: str = "cortex"

    # LLM
    llm_provider: str = "openai"  # or "anthropic", etc.
    llm_api_key: str = ""
    llm_model: str = "gpt-4o"

    # MQTT
    mqtt_broker_url: str = "mqtt://localhost:1883"

    # App
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"
```

#### 0.2 Postgres Schema
```
migrations/
├── 001_initial_schema.sql
└── 002_sessions.sql
```

**Tables:**
- `sessions` — conversation sessions
- `messages` — individual messages per session
- `schema_migrations` — migration tracking

```sql
-- migrations/001_initial_schema.sql
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE sessions (
    id UUID PRIMARY KEY,
    state VARCHAR(20) NOT NULL DEFAULT 'created',
    created_at TIMESTAMP DEFAULT NOW(),
    last_activity_at TIMESTAMP DEFAULT NOW(),
    ended_at TIMESTAMP NULL,
    metadata JSONB DEFAULT '{}'
);

CREATE TABLE messages (
    id UUID PRIMARY KEY,
    session_id UUID REFERENCES sessions(id),
    role VARCHAR(20) NOT NULL,  -- user, assistant, system
    content TEXT NOT NULL,
    tool_calls JSONB NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_messages_session ON messages(session_id);
CREATE INDEX idx_messages_created ON messages(created_at);
```

#### 0.3 Docker Setup
```
docker-compose.yml
Dockerfile
docker/
└── Dockerfile.app
```

- Single `cortex` container for v1 (no microservices yet)
- PostgreSQL container
- Mosquitto container
- Volume for Postgres data

```yaml
# docker-compose.yml
services:
  cortex:
    build: .
    ports:
      - "8000:8000"
    environment:
      - POSTGRES_HOST=postgres
      - MQTT_BROKER_URL=mqtt://mosquitto:1883
    depends_on:
      - postgres
      - mosquitto

  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: cortex
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: cortex
    volumes:
      - postgres_data:/var/lib/postgresql/data

  mosquitto:
    image: eclipse-mosquitto:2
    ports:
      - "1883:1883"
    volumes:
      - ./docker/mosquitto.conf:/mosquitto/config/mosquitto.conf

volumes:
  postgres_data:
```

#### 0.4 Logging
```
src/cortex/logging.py
```
- Structured JSON logging
- Per-module logger with `trace_id` propagation
- Log levels configurable via env

---

## Wave 1: Core Abstractions

> Depends on: Wave 0 (Config)

### Goals
- Event Bus operational
- LLM client working with at least one provider
- Session management functional

### Deliverables

#### 1.1 Event Bus
```
src/cortex/events/
├── __init__.py
├── base.py      # BaseEvent, EventMetadata
├── schemas.py   # Event payload models
├── bus.py       # EventBus implementation
└── registry.py  # Event type constants
```

**Event Bus API:**
```python
class EventBus:
    async def publish(self, event: BaseEvent) -> None: ...
    async def subscribe(self, event_type: str, handler: Callable) -> None: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
```

**Base Event:**
```python
class BaseEvent(BaseModel):
    type: str                           # e.g., "user.message"
    version: str = "1.0"
    metadata: EventMetadata
    payload: dict

class EventMetadata(BaseModel):
    timestamp: datetime
    session_id: str | None
    source_module: str
    trace_id: str
    salience: float = 0.5
```

#### 1.2 LLM Abstraction
```
src/cortex/llm/
├── __init__.py
├── base.py           # LLMClient abstract class
├── resource_manager.py  # Priority queue
├── chat_result.py    # ChatResult model
└── clients/
    ├── __init__.py
    └── openai.py     # OpenAI implementation
```

**LLM Client Interface:**
```python
class LLMClient(ABC):
    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        generation_config: GenerationConfig | None = None
    ) -> ChatResult: ...

    @abstractmethod
    def translate_tools(
        self,
        tools: list[ToolDefinition]
    ) -> list[dict]: ...  # Provider-specific format

    @abstractmethod
    def translate_tool_call(
        self,
        raw_call: dict
    ) -> ToolCall: ...
```

**Usage:**
```python
# Per-module instantiation
interaction_llm = LLMClientFactory.create(provider="openai")
memory_llm = LLMClientFactory.create(provider="openai")

# Via resource manager (future)
resource_manager = LLMResourceManager()
result = await resource_manager.request(
    priority=0,  # 0=highest
    prompt=messages,
    tools=tools
)
```

#### 1.3 Session Management
```
src/cortex/sessions/
├── __init__.py
├── service.py       # Session lifecycle
├── repository.py   # Postgres operations
└── models.py       # Session, Message models
```

**Session Lifecycle:**
```
created → active → idle → ended
```

**API:**
```python
class SessionService:
    async def create_session(self) -> Session: ...
    async def get_session(self, session_id: UUID) -> Session | None: ...
    async def add_message(self, session_id: UUID, message: Message) -> Message: ...
    async def get_conversation_history(
        self,
        session_id: UUID,
        limit: int = 50
    ) -> list[Message]: ...
    async def update_state(self, session_id: UUID, state: SessionState) -> None: ...
```

---

## Wave 2: Independent Modules

> Depends on: Wave 0 (Config), Wave 1 (Event Bus)

### Goals
- Tools can be registered and executed
- Minion events can be received and parsed

### Deliverables

#### 2.1 Tool Ecosystem
```
src/cortex/tools/
├── __init__.py
├── registry.py      # Tool registration & discovery
├── executor.py      # Tool execution
├── meta/
│   ├── __init__.py
│   ├── file_read.py
│   ├── file_write.py
│   ├── shell.py
│   ├── grep.py
│   └── http_request.py
└── models.py        # Tool, ToolDefinition models
```

**Tool Interface:**
```python
class Tool(ABC):
    id: UUID
    name: str
    description: str
    input_schema: dict  # JSON Schema
    output_schema: dict | None

    @abstractmethod
    async def execute(self, arguments: dict) -> ToolResult: ...

class ToolResult(BaseModel):
    success: bool
    result: dict | None
    error: str | None
```

**Tool Registry:**
```python
class ToolRegistry:
    def register(self, tool: Tool) -> None: ...
    def get_tool(self, name: str) -> Tool | None: ...
    def list_tools(self, category: str | None = None) -> list[Tool]: ...
    def get_schemas(self) -> list[ToolDefinition]: ...
```

**Executor:**
```python
class ToolExecutor:
    def __init__(self, registry: ToolRegistry): ...

    async def execute(
        self,
        tool_name: str,
        arguments: dict,
        timeout: int = 60
    ) -> ToolResult: ...
```

#### 2.2 Minion Protocol
```
src/cortex/minion_api/
├── __init__.py
├── mqtt_client.py   # MQTT subscriber
├── auth.py          # Token management
├── event_handler.py # Parse & validate events
└── models.py        # Minion-specific models
```

**MQTT Client:**
```python
class MinionMQTTClient:
    def __init__(
        self,
        broker_url: str,
        token: str,
        minion_id: str
    ): ...

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def subscribe(self, handler: Callable) -> None: ...
```

**Event Handler:**
```python
class MinionEventHandler:
    def __init__(
        self,
        event_bus: EventBus,
        minion_registry: MinionRegistry
    ): ...

    async def handle_message(
        self,
        topic: str,
        payload: bytes
    ) -> None: ...
```

**Minion Registry (simple for v1):**
```python
class MinionRegistry:
    # Map of minion_id -> MinionInfo
    minions: dict[str, MinionInfo]

    def register(self, minion_id: str, info: MinionInfo) -> None: ...
    def get(self, minion_id: str) -> MinionInfo | None: ...
    def list_active(self) -> list[MinionInfo]: ...
```

---

## Wave 3: Agentic Loop (Core)

> Depends on: Wave 1 (Event Bus, LLM), Wave 2 (Tools, Memory)

### Goals
- Core agentic loop implemented: Think → Act → Observe → Respond
- Two operating modes: Chat (interactive) and Goal (background)
- Context window management to handle long conversations

### The Agentic Loop

The Agentic Loop is the heart of Cortex. It runs the Think → Act → Observe → Respond cycle:

```
┌─────────────────────────────────────────────────────────────────┐
│                        AGENTIC LOOP                             │
│                                                                  │
│    ┌──────────────┐                                             │
│    │   RECEIVE    │  ← User message or goal                     │
│    └──────┬───────┘                                             │
│           │                                                      │
│           ▼                                                      │
│    ┌──────────────┐                                             │
│    │   CONTEXT    │  ← Assemble context from memory, session    │
│    └──────┬───────┘                                             │
│           │                                                      │
│           ▼                                                      │
│    ┌──────────────┐     ┌─────────────┐                          │
│    │    THINK     │────►│    ACT      │                          │
│    │    (LLM)     │     │   (Tools)   │                          │
│    └──────┬───────┘     └──────▲──────┘                          │
│           │                    │                                  │
│           │   No              │ Tool calls                       │
│           │   ┌────────────────┘                                  │
│           │   │                                                  │
│           ▼   ▼                                                  │
│    ┌──────────────┐                                             │
│    │   RESPOND    │  ← Final response to user                   │
│    └──────────────┘                                             │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Deliverables

#### 3.1 Context Builder
```
src/cortex/agentic/
├── __init__.py
├── context_builder.py   # Assemble reasoning context
├── reasoner.py         # LLM decision making
├── executor.py         # Tool execution
├── conversation.py     # Context window management
├── loop.py             # Main loop orchestration
└── models.py           # Decision, Mode, etc.
```

**Context Builder:**
```python
class ContextBuilder:
    def __init__(
        self,
        session_service: SessionService,
        memory_service: MemoryService,
        tool_registry: ToolRegistry,
        personality_manager: PersonalityManager
    ): ...

    async def build(
        self,
        session_id: UUID,
        user_message: str,
        mode: Mode,  # chat | goal
        goal_id: UUID | None = None
    ) -> Context:
        """Assemble all context needed for LLM reasoning."""
        # 1. Get conversation history (last 20 messages)
        history = await self.session_service.get_history(session_id, limit=20)

        # 2. Get relevant facts (query memory)
        facts = await self.memory_service.get_relevant(
            query=user_message,
            limit=10
        )

        # 3. Get available tools
        tools = self.tool_registry.get_schemas()

        # 4. Get personality context
        personality = await self.personality_manager.get(session_id)

        # 5. Get goal context (if applicable)
        goal_context = None
        if mode == Mode.GOAL and goal_id:
            goal = await self.goal_store.get(goal_id)
            goal_context = GoalContext(goal=goal, progress=...)

        return Context(
            conversation=history,
            facts=facts,
            tools=tools,
            personality=personality,
            goal=goal_context,
            timestamp=datetime.utcnow()
        )
```

#### 3.2 Reasoner (LLM)
```python
class Reasoner:
    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry
    ): ...

    async def reason(
        self,
        context: Context,
        max_tool_calls: int = 10
    ) -> Decision:
        """
        Given context, decide what to do next.
        
        Returns:
        - Decision.respond(text): We're done, return to user
        - Decision.execute_tools(tool_calls): Execute tools, continue loop
        - Decision.ask_question(question): Need clarification
        """
        # Build prompt with system instructions
        prompt = self._build_prompt(context)

        # Call LLM with tools
        result = await self.llm_client.chat(
            messages=prompt,
            tools=context.tools,
            generation_config=GenerationConfig(
                tool_choice="auto",
                max_tokens=4096
            )
        )

        # Parse decision
        if result.tool_calls:
            return Decision.execute_tools(result.tool_calls)
        else:
            return Decision.respond(result.message)
```

#### 3.3 Executor (Tools)
```python
class Executor:
    def __init__(
        self,
        tool_registry: ToolRegistry,
        tool_executor: ToolExecutor,
        event_bus: EventBus
    ): ...

    async def execute(
        self,
        tool_calls: list[ToolCall],
        strategy: ExecutionStrategy = ExecutionStrategy.SEQUENTIAL
    ) -> list[ToolResult]:
        """
        Execute tool calls and return results.
        
        - Sequential: Execute one at a time (for dependent tools)
        - Parallel: Execute all at once (for independent tools)
        """
        if strategy == ExecutionStrategy.PARALLEL:
            tasks = [self._execute_single(call) for call in tool_calls]
            return await asyncio.gather(*tasks)
        else:
            results = []
            for call in tool_calls:
                result = await self._execute_single(call)
                results.append(result)
                # Stop on failure (unless idempotent)
                if not result.success and not call.idempotent:
                    break
            return results

    async def _execute_single(self, call: ToolCall) -> ToolResult:
        """Execute a single tool call with error handling."""
        tool = self.tool_registry.get(call.name)
        if not tool:
            return ToolResult(success=False, error=f"Tool '{call.name}' not found")

        try:
            return await self.tool_executor.execute(
                tool_name=call.name,
                arguments=call.arguments,
                timeout=call.timeout or 60
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))
```

#### 3.4 Conversation Manager (Context Window)
```python
class ConversationManager:
    """
    Manages conversation context to fit within LLM token limits.
    """
    
    MAX_TOKENS = 128_000

    def truncate_for_llm(
        self,
        messages: list[Message],
        available_tools: list[ToolDefinition],
        max_tokens: int = 128_000
    ) -> list[Message]:
        """
        Truncate conversation to fit within token limit.
        
        Strategy:
        1. Reserve space for system prompt
        2. Reserve space for tool schemas
        3. Add messages from back to front until limit
        4. Truncate long tool results to summaries
        """
        # Implementation in AGENTIC_LOOP.md
        pass
```

#### 3.5 Main Loop
```python
class AgentLoop:
    """
    Core agentic loop: Think → Act → Observe → Respond
    
    Two operating modes:
    - CHAT: Interactive conversation with user
    - GOAL: Background task execution
    """

    def __init__(
        self,
        context_builder: ContextBuilder,
        reasoner: Reasoner,
        executor: Executor,
        event_bus: EventBus
    ): ...

    async def run_chat(
        self,
        session_id: UUID,
        user_message: str
    ) -> ChatResponse:
        """
        Run the agentic loop for chat mode.
        
        Loop: Context → Think → [Act → Observe] → Respond
        """
        messages = [Message(role=Role.USER, content=user_message)]
        iterations = 0
        max_iterations = 20

        while iterations < max_iterations:
            # Build context
            context = await self.context_builder.build(
                session_id=session_id,
                user_message=user_message,
                mode=Mode.CHAT
            )

            # Think
            decision = await self.reasoner.reason(context)

            if isinstance(decision, Decision.Respond):
                return ChatResponse(
                    message=decision.text,
                    iterations=iterations,
                    tool_calls=[]
                )

            elif isinstance(decision, Decision.ExecuteTools):
                # Act
                results = await self.executor.execute(decision.tool_calls)

                # Observe: add results to messages
                messages.extend(self._tool_messages(decision.tool_calls, results))

                iterations += 1

        # Safety: max iterations
        raise MaxIterationsError(max_iterations)

    async def run_goal(
        self,
        goal_id: UUID,
        user_message: str | None = None
    ) -> GoalResult:
        """
        Run the agentic loop for goal mode.
        Longer-running, may pause/resume.
        """
        # Similar to run_chat but with goal context
        # Higher max iterations (100)
        # Emits goal.status events
        pass
```

### Memory Module (from Wave 2)

The Memory Module provides ambient context for the loop:

```
src/cortex/memory/
├── __init__.py
├── fact_store.py    # Postgres facts storage
├── extractor.py     # LLM fact extraction
├── hierarchy.py     # Fact hierarchy management
└── models.py        # Fact, Concept models
```

**Key method for Agentic Loop:**
```python
class MemoryService:
    async def get_relevant(
        self,
        query: str,
        limit: int = 10,
        session_id: UUID | None = None
    ) -> list[Fact]:
        """
        Get facts relevant to current query.
        
        Retrieval strategy:
        1. Semantic search for query relevance
        2. Boost facts from current session
        3. Boost recent facts
        4. Boost high-confidence facts
        """
        pass
```

### Interaction Module (Thin Interface)

Interaction Module is a "thin interface" that calls the Agentic Loop:

```
src/cortex/interaction/
├── __init__.py
├── routes.py         # API endpoints
├── renderer.py      # Response formatting
└── personality.py   # Personality adaptation
```

**Note:** Interaction Module does NOT contain the agentic loop. It:
- Receives HTTP requests
- Calls Execution Module's Agentic Loop
- Formats responses for the user

```python
class InteractionService:
    def __init__(
        self,
        agent_loop: AgentLoop,  # ← Calls the loop
        session_service: SessionService
    ): ...

    async def handle_message(
        self,
        session_id: UUID,
        content: str,
        mode: str = "chat"
    ) -> Response:
        # Just calls the agent loop, returns result
        if mode == "chat":
            return await self.agent_loop.run_chat(session_id, content)
        else:
            return await self.agent_loop.run_goal(session_id, content)
```

### Learning Module (Stub for MVP)

```
src/cortex/learning/
├── __init__.py
└── stub.py           # Stub implementation for MVP
```

**For MVP:** Learning Module stores raw events for later analysis. Full pattern detection comes later.

```python
class LearningService:
    # Subscribes to all events
    # Stores raw events for future analysis
    # Emits: pattern.detected, preference.learned (stubs)
    pass
```

---

## Wave 4: Orchestration

> Depends on: Wave 3 (Agentic Loop, Memory, Interaction)

### Goals
- Execution Module wraps the Agentic Loop for goal execution
- API Gateway ties everything together
- Health checks and monitoring

### Deliverables

#### 4.1 Execution Module
```
src/cortex/execution/
├── __init__.py
├── agent_loop.py    # ← Agentic Loop lives here
├── goal_store.py    # Goal persistence
├── worker.py        # Worker spawning (for complex goals)
└── models.py
```

**Execution Module** owns the Agentic Loop and uses it for both chat and goals:

```python
class ExecutionModule:
    """
    Execution Module wraps the Agentic Loop.
    
    Responsibilities:
    - Owns the Agentic Loop
    - Goal lifecycle management
    - Worker spawning for complex goals
    - Emits goal.* events
    """

    def __init__(
        self,
        agent_loop: AgentLoop,  # ← From Wave 3
        goal_store: GoalStore,
        event_bus: EventBus
    ): ...

    async def handle_chat(
        self,
        session_id: UUID,
        message: str
    ) -> ChatResponse:
        """Handle chat via Agentic Loop."""
        return await self.agent_loop.run_chat(session_id, message)

    async def create_and_run_goal(
        self,
        description: str,
        priority: str = "normal",
        deadline: datetime | None = None
    ) -> Goal:
        """Create a goal and run it via Agentic Loop."""
        goal = await self.goal_store.create(description, priority, deadline)

        # Emit goal.created
        await self.event_bus.publish(GoalCreatedEvent(
            type="goal.created",
            payload=GoalPayload(goal_id=goal.id)
        ))

        # Run goal via loop (async, returns immediately)
        asyncio.create_task(self.agent_loop.run_goal(goal.id, description))

        return goal

    async def resume_goal(
        self,
        goal_id: UUID
    ) -> GoalResult:
        """Resume a paused/failed goal."""
        # Read goal from store
        goal = await self.goal_store.get(goal_id)

        # Emit goal.resumed
        await self.event_bus.publish(GoalResumedEvent(...))

        # Run via loop
        return await self.agent_loop.run_goal(goal_id, goal.description)

    async def handle_tool_result(
        self,
        result: ToolResult,
        goal_id: UUID
    ) -> None: ...

    # Subscribes to: goal.created, recommendation.executed
    # Emits: goal.status, goal.completed, goal.failed, module.spawn
```

**Goal Model:**
```python
class Goal(BaseModel):
    id: UUID
    description: str
    status: GoalStatus  # created, in_progress, completed, failed
    priority: str
    deadline: datetime | None
    steps: list[GoalStep]
    created_at: datetime
    completed_at: datetime | None

class GoalStep(BaseModel):
    tool_name: str
    arguments: dict
    status: StepStatus
    result: ToolResult | None
```

#### 4.2 API Gateway
```
src/cortex/api/
├── __init__.py
├── main.py           # FastAPI app
├── routes.py         # Endpoints
├── dependencies.py   # Dependency injection
└── middleware.py     # Tracing, logging
```

**Endpoints:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `POST /chat` | POST | Send message, receive response |
| `GET /chat/{session_id}` | GET | Get conversation history |
| `GET /sessions/{id}` | GET | Get session details |
| `POST /goals` | POST | Create a goal |
| `GET /goals/{id}` | GET | Get goal status |
| `GET /health` | GET | Health check |
| `GET /admin/minions` | GET | List minions |
| `POST /admin/minions/{id}/token` | POST | Generate minion token |

**Chat Endpoint:**
```python
@router.post("/chat")
async def chat(
    message: ChatRequest,
    deps: Depends(get_chat_service)
) -> ChatResponse:
    """
    Send a message to the assistant.

    Returns streaming response with:
    - text: assistant message
    - tool_calls: any tools invoked
    - session_id: conversation session
    """
    result = await deps.interaction_service.handle_message(
        session_id=message.session_id,
        content=message.content,
        mode=message.mode
    )
    return result
```

**Dependency Injection:**
```python
# src/cortex/api/dependencies.py
async def get_event_bus() -> EventBus:
    # Shared EventBus instance
    ...

async def get_llm_client() -> LLMClient:
    # OpenAI/Anthropic client
    ...

async def get_tool_executor() -> ToolExecutor:
    # Tool executor with registry
    ...

async def get_interaction_service(
    llm: LLMClient,
    session: SessionService,
    memory: MemoryService,
    executor: ToolExecutor,
    bus: EventBus
) -> InteractionService:
    ...
```

---

## Wave 5: Integration & Polish

### Goals
- Full system runs end-to-end
- First minion implementation works
- Documentation complete

### Deliverables

#### 5.1 Full System Integration
```
src/cortex/
├── __init__.py
├── main.py           # Application entry point
├── app.py            # Cortex application setup
└── shutdown.py      # Graceful shutdown
```

**Main Application:**
```python
# src/cortex/main.py
async def main():
    # 1. Load config
    settings = Settings()

    # 2. Setup logging
    setup_logging(settings.log_level)

    # 3. Initialize database
    await run_migrations()
    db_pool = await create_db_pool(settings)

    # 4. Initialize event bus
    event_bus = EventBus()

    # 5. Initialize services
    session_service = SessionService(db_pool)
    llm_client = LLMClientFactory.create(settings.llm_provider)
    tool_registry = ToolRegistry()
    tool_executor = ToolExecutor(tool_registry)
    memory_service = MemoryService(...)
    interaction_service = InteractionService(...)
    minion_mqtt = MinionMQTTClient(...)

    # 6. Subscribe handlers to event bus
    memory_service.subscribe(event_bus)
    interaction_service.subscribe(event_bus)

    # 7. Start services
    await event_bus.start()
    await minion_mqtt.connect()

    # 8. Start API
    app = create_app(
        interaction_service=interaction_service,
        session_service=session_service,
        ...
    )
    await run_app(app, settings.app_host, settings.app_port)

    # 9. Wait for shutdown
    await wait_for_shutdown()

    # 10. Cleanup
    await minion_mqtt.disconnect()
    await event_bus.stop()
    await db_pool.close()
```

#### 5.2 First Minion Implementation
```
src/minion/
├── __init__.py
├── main.py           # CLI entry point
├── config.py         # Minion config
├── mqtt_client.py    # MQTT publisher
├── sensors/
│   ├── __init__.py
│   ├── location.py   # GPS location
│   └── calendar.py  # Calendar events
└── events/
    ├── __init__.py
    └── schemas.py    # Minion event schemas
```

**Minion Config:**
```yaml
# minion_config.yaml
minion:
  id: "phone-001"
  type: "phone"
  broker_url: "mqtt://192.168.1.100:1883"
  token: "generated-from-cortex-ui"

sensors:
  location:
    enabled: true
    interval_seconds: 300  # 5 minutes
    significant_change_meters: 100

  calendar:
    enabled: true
    sync_interval_minutes: 15

batch:
  max_size: 50
  flush_interval_seconds: 60
```

**CLI:**
```bash
# Start minion
python -m minion --config minion_config.yaml

# Or with env vars
CORTEX_BROKER_URL=mqtt://192.168.1.100:1883 \
CORTEX_MINION_TOKEN=abc123 \
CORTEX_MINION_ID=phone-001 \
python -m minion
```

#### 5.3 Documentation
- [ ] README.md with project overview
- [ ] Getting Started guide
- [ ] Configuration reference
- [ ] API documentation (OpenAPI/Swagger)
- [ ] Deployment guide

---

## Implementation Order Per Wave

### Wave 0: Infrastructure Foundation

```
Week 1-2
├── 0.1 Config system
│   └── TDD: Test config loading, env var overrides
├── 0.2 Postgres schema
│   └── TDD: Test migrations, CRUD operations
├── 0.3 Docker setup
│   └── Test: docker-compose up/down
└── 0.4 Logging
    └── TDD: Test structured logging, trace_id propagation
```

### Wave 1: Core Abstractions

```
Week 3-4
├── 1.1 Event Bus
│   ├── Basic publish/subscribe
│   ├── Salience filtering
│   ├── Queue depth limits
│   └── TDD: Test pub/sub, concurrent handlers
├── 1.2 LLM Abstraction
│   ├── Abstract base class
│   ├── OpenAI implementation
│   ├── Tool translation
│   └── TDD: Test chat, tool calls
└── 1.3 Session Management
    ├── CRUD operations
    ├── State machine
    └── TDD: Test session lifecycle
```

### Wave 2: Independent Modules

```
Week 5-6
├── 2.1 Tool Ecosystem
│   ├── Tool base class
│   ├── Registry (register, list, get)
│   ├── Executor (validate, execute, timeout)
│   ├── Meta tools: file_read, file_write, shell, grep
│   └── TDD: Test tool execution
└── 2.2 Minion Protocol
    ├── MQTT client (connect, disconnect, subscribe)
    ├── Token auth
    ├── Event parsing
    ├── Minion registry
    └── TDD: Test MQTT receive, event handling
```

### Wave 3: LLM-Powered Modules

```
Week 7-8
├── 3.1 Memory Module
│   ├── Fact store (CRUD, query)
│   ├── Hierarchy management
│   ├── Fact extractor (LLM)
│   ├── Subscriptions to events
│   └── TDD: Test fact extraction, storage
├── 3.2 Interaction Module
│   ├── Prompt builder
│   ├── Session integration
│   ├── Tool execution via LLM
│   ├── Personality adaptation (stub)
│   └── TDD: Test chat flow
└── 3.3 Learning Module (stub)
    └── Store raw events for future analysis
```

### Wave 4: Orchestration

```
Week 9-10
├── 4.1 Execution Module
│   ├── Goal CRUD
│   ├── Tool orchestration
│   ├── Progress tracking
│   ├── Subscriptions
│   └── TDD: Test goal execution
└── 4.2 API Gateway
    ├── FastAPI setup
    ├── /chat endpoint
    ├── /sessions endpoints
    ├── /goals endpoints
    ├── /admin/minions endpoints
    ├── /health endpoint
    ├── OpenAPI docs
    └── TDD: Test all endpoints
```

### Wave 5: Integration

```
Week 11-12
├── 5.1 Full System Integration
│   ├── Application startup/shutdown
│   ├── Service wiring
│   ├── Error handling
│   └── E2E tests
├── 5.2 First Minion
│   ├── CLI interface
│   ├── Location sensor
│   ├── MQTT publishing
│   └── Test: Phone → Cortex
└── 5.3 Documentation
    ├── README.md
    ├── Getting Started
    └── API docs
```

---

## Testing Strategy

### Unit Tests
- Per-module tests in `tests/unit/<module>/`
- Mock external dependencies (LLM, DB, MQTT)
- Aim for 80% coverage

### Integration Tests
- `tests/integration/`
- Test module interactions
- Use test containers (Postgres, Mosquitto)

### E2E Tests
- `tests/e2e/`
- Full chat flow: user message → LLM → tool → response
- Minion → Cortex flow

---

## CI/CD Pipeline

```
.github/
└── workflows/
    ├── ci.yml      # On PR: lint, type-check, unit tests
    ├── test.yml    # On merge: integration tests
    └── deploy.yml  # On tag: Docker build, push
```

---

## File Structure

```
botti-agent/
├── src/
│   └── cortex/
│       ├── __init__.py
│       ├── config.py
│       ├── logging.py
│       ├── main.py
│       │
│       ├── events/              # Event bus, schemas
│       ├── llm/                 # LLM abstraction
│       ├── sessions/            # Session management
│       │
│       ├── tools/               # Tool ecosystem
│       │   ├── registry.py
│       │   ├── executor.py
│       │   └── meta/            # Built-in tools
│       │
│       ├── minion_api/          # Minion protocol handling
│       │
│       ├── memory/               # Memory module
│       │   ├── fact_store.py
│       │   ├── extractor.py
│       │   └── hierarchy.py
│       │
│       ├── agentic/              # ← Agentic Loop (Wave 3)
│       │   ├── context_builder.py
│       │   ├── reasoner.py
│       │   ├── executor.py
│       │   ├── conversation.py
│       │   └── loop.py
│       │
│       ├── interaction/          # Thin interface (API, rendering)
│       │
│       ├── learning/            # Learning module (stub for MVP)
│       │
│       ├── execution/            # Execution module (wraps Agentic Loop)
│       │   ├── agent_loop.py    # Re-exports from agentic/
│       │   ├── goal_store.py
│       │   └── worker.py
│       │
│       └── api/                  # API Gateway
│
├── src/
│   └── minion/
│       ├── __init__.py
│       ├── main.py
│       ├── config.py
│       ├── mqtt_client.py
│       └── sensors/
│
├── migrations/
│   ├── 001_initial_schema.sql
│   └── 002_sessions.sql
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
│
├── docker/
│   ├── Dockerfile.app
│   └── mosquitto.conf
│
├── docker-compose.yml
├── pyproject.toml
├── .env.example
├── README.md
├── ARCHITECTURE.md
├── AGENTIC_LOOP.md           # ← New: Detailed loop design
├── IMPLEMENTATION_PLAN.md
└── package.json  # For pi coding agent metadata
```

---

## Key Decisions to Make Before Implementation

| Decision | Options | Recommendation |
|----------|---------|----------------|
| **Web framework** | FastAPI vs Flask vs Litestar | FastAPI (async native, auto-docs) |
| **DB ORM** | SQLAlchemy vs raw psycopg vs Pydantic | SQLAlchemy async (better async story) |
| **MQTT library** | mqttio vs aiomqtt vs hbmqtt | aiomqtt (active, simple) |
| **LLM provider** | OpenAI only vs multiple | OpenAI only for v1 |
| **Minion language** | Python vs Go vs Rust | Python (shares code with Cortex) |
| **Session storage** | Postgres vs Redis | Postgres (already in stack) |
| **Config format** | YAML only vs JSON vs TOML | YAML (as specified in ARCHITECTURE.md) |

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| LLM integration complexity | Abstract behind interface, mock for tests |
| MQTT reliability | Start with QoS 1, add persistence checks |
| Fact extraction quality | Keep v1 extraction simple, iterate on prompts |
| Scope creep | Stick to wave deliverables, defer to v2 |

---

## Success Criteria

After Wave 5:

### Core Agentic Loop (Wave 3)
- [ ] Agentic Loop executes: Context → Think → Act → Respond cycle
- [ ] LLM can reason and decide to use tools
- [ ] Tools execute and results feed back into loop
- [ ] Loop terminates with text response (or max iterations)
- [ ] Context window managed (long conversations truncated)

### Chat Mode (Wave 4)
- [ ] `POST /chat` returns a response from LLM
- [ ] Tools can be executed via chat ("read file X")
- [ ] Multi-tool conversations work (tool → result → tool → response)
- [ ] Conversation history available across messages

### Goal Mode (Wave 4)
- [ ] `POST /goals` creates and runs a goal
- [ ] Long-running goals can be paused/resumed
- [ ] Goal progress tracked and events emitted

### Full System (Wave 5)
- [ ] Minion location events flow to Cortex
- [ ] Facts are extracted and stored in Postgres
- [ ] Conversation history persists across sessions
- [ ] Docker compose brings up entire stack
- [ ] Health endpoint returns healthy status

---

*Last updated: 2026-04-29*
