# Agentic Loop Design

> How Cortex thinks, acts, and reasons. The heart of the agent system.
> Created: 2026-04-29

---

## Overview

The **Agentic Loop** is the core reasoning cycle that powers Cortex. It connects:
- **User input** (chat, goals)
- **LLM reasoning** (thinking)
- **Tool execution** (acting)
- **Memory context** (knowing)
- **Learning feedback** (improving)

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
│    ┌──────────────┐     ┌─────────────┐                         │
│    │    THINK     │────►│    ACT      │                         │
│    │    (LLM)     │     │   (Tools)   │                         │
│    └──────┬───────┘     └──────▲──────┘                         │
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

---

## Design Principles

1. **Context-first reasoning** — LLM always receives relevant context before thinking
2. **Explicit loop stages** — THINK, ACT, OBSERVE are distinct phases
3. **Tool-use as first-class citizen** — Tools are not a fallback; they're a primary capability
4. **Failure is informative** — Tool errors feed back into reasoning
5. **Memory is ambient** — Facts are always available, not explicitly retrieved per turn

---

## Two Operating Modes

Cortex serves two primary use cases:

### Mode 1: Chat (Interactive Conversation)

**Purpose:** Quick questions, casual conversation, one-off tasks

**Characteristics:**
- Single session, potentially multi-turn
- User expects immediate response
- Tools optional (may be zero tool calls)
- Loop terminates when LLM returns text response

**Example flows:**

```
User: "What's my schedule today?"
→ Context: fetch calendar facts
→ Think: LLM reasons about schedule
→ Act: none (no tools needed)
→ Respond: "You have a meeting at 2pm..."

User: "Read the README and summarize it"
→ Context: file read tool available
→ Think: LLM decides to use file_read tool
→ Act: file_read("/path/to/README")
→ Observe: file contents returned
→ Think: LLM summarizes the content
→ Respond: "The README describes..."

User: "Deploy the app to production"
→ Context: deploy tool available
→ Think: LLM plans deployment steps
→ Act: deploy tool → confirmation needed
→ Observe: deployment result
→ Respond: "Deployment complete!"
```

### Mode 2: Goals (Background Tasks)

**Purpose:** Long-running, multi-step tasks delegated by user

**Characteristics:**
- Created via explicit goal, not chat
- May span hours or days
- Complex reasoning with many iterations
- Progress tracked and reported
- Can be paused/resumed

**Example flow:**

```
User: "Find all TODO comments in my codebase and create a task list"

Goal created: goal.created event
↓
Loop iteration 1:
  → Context: codebase location, search tool
  → Think: need to find all TODO files
  → Act: grep tool for "TODO" in project
  → Observe: 47 TODO comments found across 12 files
  
Loop iteration 2:
  → Context: previous results, task creation tool
  → Think: need to organize todos into tasks
  → Act: parse todos, categorize by file
  → Observe: organized list
  
Loop iteration 3:
  → Context: organized list, task creation tool
  → Think: create task items
  → Act: create_tasks([...])
  → Observe: 23 tasks created
  
Goal completed: goal.completed event
↓
Respond: "Found 47 TODOs, created 23 tasks"
```

---

## Loop Architecture

### Core Components

```
┌─────────────────────────────────────────────────────────────────┐
│                        AGENTIC LOOP                             │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │                    AgentLoop                                │ │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐   │ │
│  │  │ Context     │  │ Reasoner    │  │ Executor        │   │ │
│  │  │ Builder     │  │ (LLM)       │  │ (Tools)         │   │ │
│  │  └─────────────┘  └─────────────┘  └─────────────────┘   │ │
│  │         │                │                 │              │ │
│  │         │                │                 │              │ │
│  │         └────────────────┼─────────────────┘              │ │
│  │                          │                                │ │
│  │                          ▼                                │ │
│  │                   ┌─────────────┐                         │ │
│  │                   │  Memory     │                         │ │
│  │                   │  Service    │                         │ │
│  │                   └─────────────┘                         │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │                    SUPPORT SERVICES                         │ │
│  │                                                              │ │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐   │ │
│  │  │ Session     │  │ Tool        │  │ Event           │   │ │
│  │  │ Service     │  │ Registry    │  │ Bus             │   │ │
│  │  └─────────────┘  └─────────────┘  └─────────────────┘   │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

### 1. Context Builder

**Responsibility:** Assemble all context needed for the LLM to reason.

**Context sources:**

| Source | What it provides | When |
|--------|------------------|------|
| **Session** | Conversation history (last N messages) | Always |
| **Memory** | Relevant facts about user, current context | Always |
| **Tools** | Available tools with schemas | Always |
| **Personality** | User's learned preferences | Always |
| **Goal** | Goal description + progress (if goal mode) | Goal mode only |
| **Minions** | Current location, activity (ambient) | Optional |

**Context trimming:**
- Limit conversation history to last 20 messages (configurable)
- Limit facts to top 10 most relevant (by recency + confidence)
- Truncate long tool results to summary

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
        """
        Assembles context for LLM reasoning.
        """
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

### 2. Reasoner (LLM)

**Responsibility:** Think about the context and decide next action.

**Decision types:**

| Decision | Output | Next Step |
|----------|--------|-----------|
| **Respond with text** | Text response | End loop |
| **Execute tool(s)** | List of tool calls | Go to Executor |
| **Create goal** | Goal description | Emit goal.created, end loop |
| **Ask clarifying question** | Question | Respond to user |
| **Request more context** | Context request | Fetch more, continue thinking |

**Reasoning model:**

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
        """
        # Build prompt
        prompt = self._build_prompt(context)

        # Call LLM
        result = await self.llm_client.chat(
            messages=prompt,
            tools=context.tools,  # Available tools
            generation_config=GenerationConfig(
                tool_choice="auto",  # Let LLM decide when to use tools
                max_tokens=4096
            )
        )

        # Parse decision
        if result.tool_calls:
            return Decision.execute_tools(result.tool_calls)
        else:
            return Decision.respond(result.message)
```

**System Prompt Structure:**

```
You are Cortex, a personal AI assistant.

PERSONALITY:
{{personality_context}}

CURRENT CONTEXT:
- Time: {{current_time}}
- Location: {{location}}
- Activity: {{activity}}

USER'S KNOWN FACTS:
{{facts_summary}}

CONVERSATION HISTORY:
{{conversation_history}}

AVAILABLE TOOLS:
{{tool_schemas}}

INSTRUCTIONS:
1. Think about what the user is asking
2. If tools can help, use them to gather information or take action
3. If you have enough information, provide a helpful response
4. Be concise but thorough
5. If something is unclear, ask for clarification

User's message: {{user_message}}
```

### 3. Executor (Tools)

**Responsibility:** Execute tool calls decided by the Reasoner.

**Execution strategy:**

| Strategy | When | Behavior |
|----------|------|----------|
| **Sequential** | Tools depend on each other | Execute one at a time, wait for result |
| **Parallel** | Independent tools | Execute all at once (faster) |
| **Batched** | Many similar tools | Group into batches |

**Tool execution:**

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
        """
        results = []

        if strategy == ExecutionStrategy.PARALLEL:
            # Execute independent tools in parallel
            tasks = [
                self._execute_single(call)
                for call in tool_calls
            ]
            results = await asyncio.gather(*tasks)
        else:
            # Sequential execution
            for call in tool_calls:
                result = await self._execute_single(call)
                results.append(result)

                # Emit event for learning
                await self.event_bus.publish(
                    Event(
                        type="tool.result",
                        payload=ToolResultPayload(
                            tool_name=call.name,
                            success=result.success,
                            duration_ms=result.duration_ms
                        )
                    )
                )

                # Stop on failure (unless tool is marked idempotent)
                if not result.success and not call.idempotent:
                    break

        return results

    async def _execute_single(self, call: ToolCall) -> ToolResult:
        """Execute a single tool call."""
        tool = self.tool_registry.get(call.name)
        if not tool:
            return ToolResult(
                success=False,
                error=f"Tool '{call.name}' not found"
            )

        try:
            result = await self.tool_executor.execute(
                tool_name=call.name,
                arguments=call.arguments,
                timeout=call.timeout or 60
            )
            return result
        except Exception as e:
            return ToolResult(
                success=False,
                error=str(e)
            )
```

### 4. Memory Service (Ambient Context)

**Responsibility:** Store and retrieve facts. Always available in context.

**Key insight:** Memory is NOT explicitly "called" during the loop. It's always present as ambient context. The Context Builder fetches relevant facts silently.

**Fact retrieval for context:**

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
        1. Semantic search (embeddings) for query relevance
        2. Boost facts from current session
        3. Boost recent facts (recency)
        4. Boost high-confidence facts
        5. Filter by type (preferences, knowledge, context)
        """
        # Get current session facts (high boost)
        session_facts = []
        if session_id:
            session_facts = await self.fact_store.get_for_session(session_id)

        # Semantic search
        query_embedding = await self.embedding_service.encode(query)
        semantic_facts = await self.fact_store.search(
            embedding=query_embedding,
            limit=limit * 2
        )

        # Merge and rank
        all_facts = {f.id: f for f in session_facts + semantic_facts}

        # Score and rank
        ranked = []
        for fact in all_facts.values():
            score = self._score_fact(fact, query, session_id)
            ranked.append((score, fact))

        ranked.sort(key=lambda x: x[0], reverse=True)
        return [f for _, f in ranked[:limit]]

    def _score_fact(self, fact: Fact, query: str, session_id: UUID) -> float:
        """Score a fact for relevance."""
        score = 0.0

        # Confidence boost
        score += fact.confidence * 0.3

        # Recency boost (exponential decay)
        if fact.last_accessed_at:
            age_hours = (datetime.utcnow() - fact.last_accessed_at).total_seconds() / 3600
            score += math.exp(-age_hours / 24) * 0.2

        # Session boost
        if fact.session_id == session_id:
            score += 0.3

        # Fact type boost (preferences are always relevant)
        if fact.type == FactType.PREFERENCE:
            score += 0.2

        return score
```

---

## The Loop Implementation

### AgentLoop Class

```python
class AgentLoop:
    """
    Core agentic loop: Think → Act → Observe → Respond
    
    Operates in two modes:
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
        user_message: str,
        *,
        max_iterations: int | None = None
    ) -> ChatResponse:
        """
        Run the agentic loop for chat mode.

        Drain wrapper over stream_chat(): consumes the generator, accumulates
        the response text from TextDeltaEvent deltas and the final metadata
        from ResponseDoneEvent, and returns the resulting ChatResponse.
        Progress events (thinking, tool start/result) and ErrorEvent are
        ignored — the generator re-raises the original exception, so errors
        (e.g. MaxIterationsError) propagate unchanged.
        """
        response_text = ""
        tools_used: list[str] = []
        iterations = 0

        async for event in self.stream_chat(
            session_id=session_id,
            user_message=user_message,
            max_iterations=max_iterations,
        ):
            match event:
                case TextDeltaEvent(delta=delta):
                    response_text += delta
                case ResponseDoneEvent(tools_used=used, iterations=iters):
                    tools_used, iterations = used, iters
                case _:
                    pass  # progress events + ErrorEvent; generator re-raises

        return ChatResponse(
            message=response_text,
            tools_used=tools_used,
            iterations=iterations,
            session_id=session_id,
        )

    async def run_goal(
        self,
        goal_id: UUID,
        user_message: str | None = None
    ) -> GoalResult:
        """
        Run the agentic loop for goal mode.
        
        Longer-running, may pause/resume.
        """
        goal = await self.goal_store.get(goal_id)
        if not goal:
            raise GoalNotFoundError(goal_id)

        # Emit goal status: in_progress
        await self.event_bus.publish(GoalStatusEvent(
            type="goal.status",
            payload=GoalStatusPayload(
                goal_id=goal_id,
                status="in_progress"
            )
        ))

        iterations = 0
        max_iterations = 100  # Higher limit for goals
        max_tool_errors = 3

        while iterations < max_iterations:
            # Build context (includes goal progress)
            context = await self.context_builder.build(
                session_id=goal.session_id,
                user_message=user_message or goal.description,
                mode=Mode.GOAL,
                goal_id=goal_id
            )

            # Think
            decision = await self.reasoner.reason(context)

            if isinstance(decision, Decision.Respond):
                # Goal complete
                result = GoalResult(
                    goal_id=goal_id,
                    status="completed",
                    message=decision.text,
                    iterations=iterations
                )
                await self._complete_goal(goal_id, result)
                return result

            elif isinstance(decision, Decision.ExecuteTools):
                # Act
                results = await self.executor.execute(decision.tool_calls)

                # Check for failures
                failures = [r for r in results if not r.success]
                if len(failures) > max_tool_errors:
                    result = GoalResult(
                        goal_id=goal_id,
                        status="failed",
                        error=f"Too many tool errors: {len(failures)}",
                        iterations=iterations
                    )
                    await self._fail_goal(goal_id, result)
                    return result

                iterations += 1

            elif isinstance(decision, Decision.CreateSubGoal):
                # Nested goal
                sub_goal_id = await self.goal_store.create(decision.sub_goal)
                await self.run_goal(sub_goal_id)

        # Safety: max iterations
        result = GoalResult(
            goal_id=goal_id,
            status="failed",
            error="Max iterations exceeded",
            iterations=iterations
        )
        await self._fail_goal(goal_id, result)
        return result
```

---

## Context Window Management

**Problem:** Conversions can get long. Sending all history to LLM is expensive and can exceed context limits.

**Solution:** Sliding window with priority.

```python
class ConversationManager:
    """
    Manages conversation context to fit within LLM limits.
    """
    
    MAX_TOKENS = 128_000  # Leave room for response
    HISTORY_PRIORITY = [
        "system",      # System prompt always first
        "user",        # User messages
        "assistant",   # Assistant responses
        "tool_result",  # Tool results (can be truncated)
    ]

    def truncate_for_llm(
        self,
        messages: list[Message],
        available_tools: list[ToolDefinition],
        max_tokens: int = 128_000
    ) -> list[Message]:
        """
        Truncate conversation to fit within token limit.
        
        Strategy:
        1. Estimate tokens for system + tools
        2. Reserve space for system
        3. Add messages from back to front until limit
        4. Truncate long tool results to summaries
        """
        # Estimate overhead
        system_tokens = self._estimate_tokens("[SYSTEM PROMPT]")
        tools_tokens = sum(self._estimate_tokens(t.schema) for t in available_tools)
        reserved = system_tokens + tools_tokens

        # Available for conversation
        available = max_tokens - reserved - 1000  # Buffer

        truncated = []
        current_tokens = 0

        # Iterate back to front (most recent first)
        for msg in reversed(messages):
            msg_tokens = self._estimate_tokens(msg)

            if current_tokens + msg_tokens > available:
                # Truncate this message
                truncated_msg = self._truncate_message(msg, available - current_tokens)
                if truncated_msg:
                    truncated.insert(0, truncated_msg)
                break

            truncated.insert(0, msg)
            current_tokens += msg_tokens

        return truncated

    def _truncate_message(self, msg: Message, max_tokens: int) -> Message:
        """Truncate a single message to fit tokens."""
        if msg.role == Role.TOOL_RESULT:
            # Summarize tool results
            return Message(
                id=msg.id,
                role=msg.role,
                content=f"[Tool output truncated: {len(msg.content)} chars]",
                created_at=msg.created_at
            )
        # For other messages, truncate content
        chars = max_tokens * 4  # Rough estimate: 4 chars per token
        if len(msg.content) > chars:
            return Message(
                id=msg.id,
                role=msg.role,
                content=msg.content[:chars] + "... [truncated]",
                created_at=msg.created_at
            )
        return msg
```

---

## Error Handling in Loop

### Error Types and Recovery

| Error | Cause | Recovery |
|-------|-------|---------|
| **LLM Timeout** | LLM not responding | Retry once, then return error |
| **Tool Not Found** | Invalid tool name | Return error to LLM, continue |
| **Tool Execution Error** | Tool threw exception | Return error, may retry or skip |
| **Rate Limited (429)** | LLM rate limit | Exponential backoff, max 3 retries |
| **Context Too Long** | Exceeded token limit | Truncate conversation, retry |
| **Max Iterations** | Infinite loop prevention | Stop loop, return partial result |

### Circuit Breaker Integration

```python
class AgentLoop:
    def __init__(self, ...):
        self.reasoner_circuit = CircuitBreaker(
            failure_threshold=5,
            open_duration=30
        )
        self.executor_circuit = CircuitBreaker(
            failure_threshold=10,
            open_duration=30
        )

    async def run_chat(self, ...):
        try:
            decision = await self.reasoner_circuit.call(
                self.reasoner.reason, context
            )
        except CircuitOpenError:
            return ChatResponse(
                message="I'm having trouble thinking right now. Please try again later.",
                error="service_unavailable"
            )

        if decision.needs_tools:
            try:
                results = await self.executor_circuit.call(
                    self.executor.execute, decision.tool_calls
                )
            except CircuitOpenError:
                return ChatResponse(
                    message="I had trouble executing that. Please try again.",
                    error="tools_unavailable"
                )
```

---

## Event Emissions

Loop progress is exposed to the loop's caller via `stream_chat()`, which yields
`LoopEvent` instances (defined in `src/cortex/agentic/events.py`, per ADR-0002).
These are caller-scoped progress signals — they are **not** published to the
event bus. Each event's `event_type` is the wire name directly, one vocabulary
with no mapping table: `thinking`, `text`, `tool_start`, `tool_done`, `done`,
`error`.

```python
async for event in stream_chat(session_id=session_id, message=message):
    # event is a LoopEvent; event.event_type is the SSE wire name
    match event.event_type:
        case "thinking":    # ThinkingEvent — a reasoning step
            ...
        case "text":        # TextDeltaEvent — a delta of the final response
            ...
        case "tool_start":  # ToolStartEvent — a tool call is about to execute
            ...
        case "tool_done":   # ToolResultEvent — a tool call finished
            ...
        case "done":        # ResponseDoneEvent — the response is complete
            ...
        case "error":       # ErrorEvent — the loop failed
            ...
```

Callers that only need the final result use the non-streaming wrapper
`run_chat()`, which drains the generator into a `ChatResponse`.

---

## Tool Definitions for the Loop

The loop needs tools that are designed for LLM interaction:

### File Read Tool

```python
class FileReadTool(Tool):
    name = "file_read"
    description = "Read the contents of a file from the filesystem."
    
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path to the file"
            },
            "start_line": {
                "type": "integer",
                "description": "Line number to start reading (1-indexed)",
                "default": 1
            },
            "max_lines": {
                "type": "integer",
                "description": "Maximum number of lines to read",
                "default": 100
            }
        },
        "required": ["path"]
    }
    
    output_schema = {
        "type": "object",
        "properties": {
            "content": {"type": "string"},
            "lines": {"type": "integer"},
            "truncated": {"type": "boolean"}
        }
    }

# Example LLM interaction:
# User: "Read the config file"
# LLM thinks: I should use file_read tool
# LLM calls: file_read(path="/app/config.yaml")
# Result: {"content": "...", "lines": 50, "truncated": false}
```

### Shell Tool

```python
class ShellTool(Tool):
    name = "shell"
    description = "Execute a shell command and return the output."
    
    input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute"
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds",
                "default": 30
            },
            "working_dir": {
                "type": "string",
                "description": "Working directory for the command",
                "default": "."
            }
        },
        "required": ["command"]
    }
```

### Grep Tool

```python
class GrepTool(Tool):
    name = "grep"
    description = "Search for patterns in files."
    
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regular expression pattern to search for"
            },
            "path": {
                "type": "string",
                "description": "Directory or file path to search in"
            },
            "recursive": {
                "type": "boolean",
                "description": "Search recursively in subdirectories",
                "default": True
            },
            "file_pattern": {
                "type": "string",
                "description": "File glob pattern (e.g., '*.py')",
                "default": "*"
            }
        },
        "required": ["pattern", "path"]
    }
```

### HTTP Request Tool

```python
class HTTPRequestTool(Tool):
    name = "http_request"
    description = "Make an HTTP request to a URL."
    
    input_schema = {
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"],
                "default": "GET"
            },
            "url": {
                "type": "string",
                "description": "The URL to request"
            },
            "headers": {
                "type": "object",
                "description": "HTTP headers to include"
            },
            "body": {
                "type": "string",
                "description": "Request body (for POST/PUT/PATCH)"
            }
        },
        "required": ["url"]
    }
```

---

## Integration with Existing Architecture

### Where the Loop Lives

**Decision:** The Agentic Loop lives in the **Execution Module**.

| Reason | Explanation |
|--------|-------------|
| **Separation of concerns** | Interaction Module handles I/O (API, rendering), Execution handles reasoning |
| **Reusable** | Same loop for chat and goals |
| **Testable** | Loop logic is isolated |
| **Consistent** | Goals and chat use the same reasoning engine |

**Updated Module Responsibilities:**

| Module | Responsibility |
|--------|---------------|
| **Interaction** | API gateway, session management, response rendering. Calls AgentLoop. |
| **Execution** | **Agentic Loop**, goal orchestration, tool execution |
| **Memory** | Fact storage and retrieval (ambient context) |
| **Learning** | Pattern detection, preference learning (offline) |

### Updated Event Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                         API GATEWAY                             │
│                                                                  │
│  POST /chat { message: "..." }                                  │
│  └─────────────────────────────────────────────────────────────►│
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │                   EXECUTION MODULE                          │ │
│  │                                                              │ │
│  │   ┌──────────────────────────────────────────────────────┐ │ │
│  │   │                    AGENTIC LOOP                      │ │ │
│  │   │                                                      │ │ │
│  │   │   Context ──► Think ──► [Act ──► Observe] ──► Respond │ │ │
│  │   │     │           │          │              │           │ │ │
│  │   │     │           │          │              │           │ │ │
│  │   │     ▼           ▼          ▼              │           │ │ │
│  │   │   Memory    Reasoner    Executor         │           │ │ │
│  │   │   Service   (LLM)       (Tools)          │           │ │ │
│  │   │                                                      │ │ │
│  │   └──────────────────────────────────────────────────────┘ │ │
│  │                                                              │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ◄──────────────────────────────────────────────────────────────┘
│  Response: { message: "..." }
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### API Changes

```python
# POST /chat — calls AgentLoop.run_chat()
class ChatRequest(BaseModel):
    session_id: UUID | None = None  # Creates new if None
    message: str
    mode: Literal["chat", "goal"] = "chat"

class ChatResponse(BaseModel):
    session_id: UUID
    message: str
    iterations: int
    tools_used: list[str]
    error: str | None = None

# POST /goals — calls AgentLoop.run_goal() (async)
class CreateGoalRequest(BaseModel):
    description: str
    priority: str = "normal"
    deadline: datetime | None = None

class GoalResponse(BaseModel):
    goal_id: UUID
    status: str  # "created", "in_progress", "completed", "failed"
    message: str | None = None
```

---

## Summary

| Component | Purpose | Key Methods |
|-----------|---------|-------------|
| **AgentLoop** | Core loop orchestrator | `run_chat()`, `run_goal()` |
| **ContextBuilder** | Assemble reasoning context | `build()` |
| **Reasoner** | LLM decision making | `reason()` |
| **Executor** | Tool execution | `execute()` |
| **MemoryService** | Ambient fact retrieval | `get_relevant()` |
| **ConversationManager** | Context window management | `truncate_for_llm()` |

**Loop Flow:**
1. **Context** — Build context from session, memory, tools
2. **Think** — LLM reasons and decides (respond or tools)
3. **Act** — Execute tool calls (if any)
4. **Observe** — Collect results
5. **Loop** — Continue until response ready
6. **Respond** — Return to user

---

## Open Questions

- [ ] Should the loop support "reflection" — LLM reviewing its own actions?
- [ ] How do we handle user interruptions mid-loop?
- [ ] Should tools be batched automatically or let LLM decide?
- [ ] How do we credit tool success/failure to LLM decisions for learning?

---

*Last updated: 2026-04-29*
