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
**Key Libraries:** Pydantic, AsyncIO, PostgreSQL
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

> The River — all modules communicate via events flowing through an in-memory event bus (asyncio Queue).
> Redis can be added later for production resilience when actual needs are understood.

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
| `goal.resumed` (not emitted) | Execution | Interaction, Learning | Task recovery at startup — direct call per ADR-0004, not a bus event |
| **Orchestration** | | | |
| `module.spawn` | Execution | (orchestration) | Spawn sub-process/worker |
| `module.terminate` | Execution | (orchestration) | Clean up sub-process |

**Minion events** flow through the same event bus as direct user input. Memory and Learning modules automatically process them to extract facts and patterns.

**Fact storage:** Facts are stored in Postgres. Modules query the DB directly — no `fact.query`/`fact.result` events.

---

## Module Ecosystem

### Design Principles

- **Peers, not hierarchy** — modules are equal participants
- **Own their own state** — each module manages its internal state, persists to shared DB
- **Reactive + Proactive** — respond to events AND initiate based on internal logic
- **Shared event bus** — In-memory asyncio Queue for MVP (Redis addable later)
- **Shared persistence** — Postgres for sessions, facts, patterns, tool registry

---

### Interaction Module

> **Thin interface** between user and Cortex brain. Routes queries, formats responses. Does NOT run the agentic loop.

| Aspect | Decision |
|--------|----------|
| Subscribes to | `user.message`, `recommendation.generated` |
| Emits | `conversation.message`, `goal.created` |
| Owns LLM | **No** (thin interface) |
| State | Session context, current mode |

**Responsibilities:**
- API gateway for chat and goal endpoints
- Session management (create, resume, archive)
- Call Execution Module's Agentic Loop for reasoning
- Render responses to user (text, tool results, recommendations)
- Manage conversation lifecycle (start, end, mode switches)
- **Query mode:** User can ask about their own life ("where do I spend most of my time?", "summarize my spending this month")

**Note:** Interaction Module is intentionally "thin". The Agentic Loop lives in Execution Module, ensuring chat and goals share the same reasoning engine.

---

### Memory Module

> Persistent storage of facts and knowledge about the user and their world. Powered by minion sensory data.

| Aspect | Decision |
|--------|----------|
| Subscribes to | All events (`*`) — watches everything |
| Emits | (writes to Postgres directly) |
| Owns LLM | Yes — fact extraction and synthesis |
| State | Facts DB (Postgres), user knowledge graph |

**Responsibilities:**
- Store and retrieve facts (user preferences, project context, people, history)
- Index facts for fast retrieval
- Fact extraction from minion data: location → "user works at X", payment → "user spent Y at Z"
- Fact extraction from conversations
- Cascade invalidation when mutable facts change

**Minion data → Facts examples:**
| Minion Event | Extracted Fact |
|-------------|----------------|
| `location` (repeated, same place, 9-5) | "user works at [venue]" |
| `location` (night, same place) | "user lives at [venue]" |
| `payment` | "user spent $X at [merchant]" |
| `payment` (monthly, same merchant) | "user has subscription to [service]" |
| `activity` (low screen time on weekends) | "user is less active on screens weekends" |

**Fact Model:**

```
Fact {
    id: UUID                      # unique identifier
    type: str                     # fact category: preference, behavior, knowledge, context
    mutability: immutable | mutable
    symbolic_repr: str            # canonical form for logic engine (e.g., "lives_in(user, Italy)")
    natural_lang_repr: str         # human readable (e.g., "I live in Italy")
    payload: JSON                 # structured data specific to fact type
    confidence: float             # 0.0-1.0
    created_at: datetime
    retracted_at: datetime | null # null = active, timestamp = retracted

    # Hierarchy tracking (frequency-adjusted tree)
    layer: int                     # tree layer (0 = hot/most accessed, n = cold/archived)
    access_count: int             # total times accessed
    last_accessed_at: datetime    # for recency weighting
}

Concept = DerivedFact {
    ...Fact fields...
    derivation_method: induction | deduction | creative
    proof_chain: str               # symbolic reasoning provided by LLM
    source_facts: [UUID]          # provenance of derived fact
    validated: bool               # logic engine approved this derivation
}
```

**Fact Types:**

| Type | Description | Examples |
|------|-------------|----------|
| `immutable` | Fundamental truths (birth, physical laws) | born(Sarah, Italy), 2+2=4 |
| `mutable` | Can change over time | lives_in(user, Italy), prefers concise responses |

**Logic Engine (PyDatalog):**
- On-demand validation — runs when a concept is proposed
- Validates symbolic reasoning chain against known facts
- If conflict detected → fact rejected, LLM must fix reasoning
- Immutable facts serve as axioms; mutable facts can be retracted

**Cascade Invalidation:**
- Mutable fact changes → system identifies all derived concepts using it (directly or indirectly)
- All downstream concepts get retracted recursively

**Recall Mechanism (Hybrid):**
1. Embedding similarity identifies semantic area of query
2. Search hot layer first (frequently accessed + recency weighted)
3. If not found, expand to warm → cold layers
4. Results merged/ranked by relevance + confidence

**Hierarchy Promotion:**
- Access count gives boost to fact recall
- Recent facts get minor boost; non-recent facts get greater boost
- Continuous adjustment based on access patterns

---

## MemoryService Interface

> Explicit service API for querying and storing facts. All modules interact with Memory through this interface, not raw SQL.

### Design Rationale

| Approach | Problem |
|----------|--------|
| Raw SQL per module | Duplication, tight coupling to schema |
| Event-based queries (`fact.query`/`fact.result`) | Async overhead, no direct returns |
| **Service API** | Clean separation, testable, evolvable |

### Service API

```python
class MemoryService:
    """
    Service API for the Memory Module.
    
    All modules query Memory through this interface.
    Backed by Postgres with optional embedding cache.
    """

    # ─────────────────────────────────────────────────────────────
    # QUERY METHODS (for Agentic Loop, Interaction, Learning)
    # ─────────────────────────────────────────────────────────────

    async def get_relevant(
        self,
        query: str,
        limit: int = 10,
        session_id: UUID | None = None,
        fact_types: list[FactType] | None = None
    ) -> list[Fact]:
        """
        Get facts relevant to a query.
        
        Retrieval strategy:
        1. Semantic search (embeddings) for query relevance
        2. Boost facts from current session
        3. Boost recent facts (recency weighted)
        4. Boost high-confidence facts
        5. Filter by fact_types if specified
        
        Returns up to `limit` facts, ranked by relevance.
        """
        ...

    async def get_by_type(
        self,
        fact_type: FactType,
        limit: int = 50,
        include_retracted: bool = False
    ) -> list[Fact]:
        """
        Get all facts of a specific type.
        
        Use for: personality traits, known preferences, learned behaviors.
        """
        ...

    async def get_context(
        self,
        dimensions: list[str] = ["time", "location", "activity"]
    ) -> dict[str, Any]:
        """
        Get current ambient context.
        
        Returns current state of contextual dimensions:
        - time: current hour, day of week
        - location: last known location, venue type
        - activity: current activity (from minions)
        
        Used by Agentic Loop for context injection.
        """
        ...

    async def get_personality_context(
        self,
        session_id: UUID | None = None
    ) -> PersonalityContext:
        """
        Get personality traits for response formatting.
        
        Merges:
        1. Learned traits from Memory (long-term)
        2. Session-specific preferences (short-term)
        3. Default traits (0.5 on all dimensions)
        """
        ...

    async def search(
        self,
        query: str,
        filters: SearchFilters | None = None,
        limit: int = 20
    ) -> list[Fact]:
        """
        Full-text + semantic search across facts.
        
        Supports:
        - Natural language queries ("where do I work?")
        - Symbolic queries ("lives_in(user, *)")
        - Filters by type, confidence, date range
        """
        ...

    # ─────────────────────────────────────────────────────────────
    # STORAGE METHODS (internal to Memory Module)
    # ─────────────────────────────────────────────────────────────

    async def store_fact(
        self,
        fact: Fact
    ) -> Fact:
        """
        Store a new fact. Handles deduplication and hierarchy init.
        """
        ...

    async def store_facts(
        self,
        facts: list[Fact]
    ) -> list[Fact]:
        """
        Batch store facts. Used by fact extraction pipeline.
        """
        ...

    async def retract_fact(
        self,
        fact_id: UUID,
        reason: str | None = None
    ) -> None:
        """
        Retract a fact (soft delete).
        
        Cascade invalidation: all concepts derived from this fact
        are also retracted.
        """
        ...

    async def update_fact(
        self,
        fact_id: UUID,
        updates: FactUpdate
    ) -> Fact:
        """
        Update a mutable fact. Triggers cascade invalidation.
        """
        ...

    # ─────────────────────────────────────────────────────────────
    # CONCEPT METHODS (derived facts)
    # ─────────────────────────────────────────────────────────────

    async def propose_concept(
        self,
        derivation: ConceptDerivation
    ) -> Concept | Rejection:
        """
        Propose a derived concept for validation.
        
        Logic engine validates the proof chain.
        Returns Concept if valid, Rejection with reason if not.
        """
        ...

    async def get_concepts(
        self,
        source_fact_id: UUID | None = None,
        method: DerivationMethod | None = None
    ) -> list[Concept]:
        """
        Get derived concepts, optionally filtered.
        """
        ...

    # ─────────────────────────────────────────────────────────────
    # HIERARCHY METHODS (internal)
    # ─────────────────────────────────────────────────────────────

    async def record_access(
        self,
        fact_id: UUID
    ) -> None:
        """
        Record that a fact was accessed.
        
        Updates access_count and last_accessed_at.
        May trigger hierarchy promotion.
        """
        ...

    async def compact_hierarchy(
        self,
        target_layer: int
    ) -> int:
        """
        Compact facts into target layer.
        
        Called periodically (e.g., nightly).
        Returns number of facts compacted.
        """
        ...
```

### Supporting Models

```python
class PersonalityContext(BaseModel):
    """Personality traits for response formatting."""
    tone: float = 0.5              # 0=sarcastic, 1=serious
    verbosity: float = 0.5         # 0=concise, 1=detailed
    formality: float = 0.5         # 0=casual, 1=formal
    humor: float = 0.5             # 0=dry, 1=enthusiastic
    directness: float = 0.5        # 0=blunt, 1=tactful
    confidence: float = 0.5        # learned confidence level
    
    # Source tracking
    sources: list[UUID] = []       # Facts this was derived from
    last_updated: datetime

class SearchFilters(BaseModel):
    """Filters for fact search."""
    fact_types: list[FactType] | None = None
    min_confidence: float | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    mutable_only: bool = False
    include_retracted: bool = False

class FactUpdate(BaseModel):
    """Allowed updates to a mutable fact."""
    payload: dict | None = None
    confidence: float | None = None
    symbolic_repr: str | None = None
    natural_lang_repr: str | None = None

class ConceptDerivation(BaseModel):
    """Derivation proposal for a concept."""
    symbolic_repr: str
    natural_lang_repr: str
    derivation_method: DerivationMethod
    proof_chain: str                # LLM's reasoning
    source_facts: list[UUID]       # Provenance
    confidence: float

class Rejection(BaseModel):
    """Why a concept was rejected."""
    reason: str
    conflicting_facts: list[UUID] = []
    suggested_fix: str | None = None
```

### Module Access Pattern

```
┌─────────────────────────────────────────────────────────────────┐
│                       MEMORY SERVICE                             │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    PUBLIC API                              │  │
│  │                                                           │  │
│  │   get_relevant()    ← Agentic Loop queries memory         │  │
│  │   get_context()      ← Ambient context injection           │  │
│  │   get_personality()  ← Response formatting                 │  │
│  │   search()           ← User queries ("where do I work?")  │  │
│  │                                                           │  │
│  └──────────────────────────────────────────────────────────┘  │
│                              │                                   │
│                              ▼                                   │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    INTERNAL COMPONENTS                    │  │
│  │                                                           │  │
│  │   ┌────────────┐  ┌────────────┐  ┌────────────────┐     │  │
│  │   │ FactStore  │  │ Extractor  │  │ Logic Engine   │     │  │
│  │   │ (Postgres) │  │   (LLM)    │  │  (PyDatalog)  │     │  │
│  │   └────────────┘  └────────────┘  └────────────────┘     │  │
│  │                                                           │  │
│  └──────────────────────────────────────────────────────────┘  │
│                              │                                   │
│                              ▼                                   │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    POSTGRES                                │  │
│  │   facts | concepts | hierarchy | context_cache             │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### Event Subscriptions (Internal)

```python
class MemoryService:
    """MemoryService also subscribes to events for fact extraction."""
    
    async def handle_event(self, event: BaseEvent) -> None:
        """Process incoming events, extract facts."""
        match event.type:
            case "user.message":
                await self._extract_from_conversation(event)
            case "conversation.message":
                await self._extract_from_conversation(event)
            case "location":
                await self._extract_location_facts(event)
            case "payment":
                await self._extract_payment_facts(event)
            case "activity":
                await self._extract_activity_facts(event)
            case _:
                pass  # Other events watched but not directly extracted
```

---

### Personality Module

> Manages learned personality traits and provides personality context for response formatting. Traits are stored in Memory and surfaced through PersonalityService.

| Aspect | Decision |
|--------|----------|
| Subscribes to | `user.feedback`, `preference.learned`, `conversation.ended` |
| Emits | (writes to Memory via MemoryService) |
| Owns LLM | No ( PersonalityManager does simple aggregation) |
| State | Personality traits (stored as facts in Memory) |

**Design Rationale:**

| Approach | Problem |
|----------|--------|
| Separate personality store | Duplication with Memory facts |
| Personality as special fact type | Works but loses discoverability |
| **PersonalityModule backed by Memory** | Unified storage, MemoryService provides context |

**Key insight:** Personality traits ARE facts (type=`preference`) with a specific payload structure. No separate storage needed.

---

**Responsibilities:**
- Aggregate personality traits from Memory facts
- Provide personality context for system prompt injection
- Handle explicit user feedback ("that was too harsh")
- Handle implicit feedback (user's communication style)
- Merge learned traits with session-specific overrides

**Trait Model:**

```python
class PersonalityTrait(BaseModel):
    """A single personality trait dimension."""
    dimension: TraitDimension
    value: float                         # 0.0 to 1.0
    confidence: float                    # How sure we are
    source: Literal["explicit", "inferred"] # How we learned it
    source_fact_id: UUID | None          # Original fact
    last_updated: datetime

class TraitDimension(str, Enum):
    TONE = "tone"                        # 0=sarcastic, 1=serious
    VERBOSITY = "verbosity"              # 0=concise, 1=detailed
    FORMALITY = "formality"              # 0=casual, 1=formal
    HUMOR = "humor"                      # 0=dry, 1=enthusiastic
    DIRECTNESS = "directness"            # 0=blunt, 1=tactful
    CONFIDENCE = "confidence"            # learned confidence level

class PersonalityProfile(BaseModel):
    """Full personality profile."""
    trait_directions: list[PersonalityTrait]
    derived_at: datetime
    source_traits_count: int             # How many facts backing this
    is_default: bool = False             # True if no learning yet
```

**Default Profile (Before Learning):**

```python
DEFAULT_PERSONALITY = PersonalityProfile(
    trait_directions=[
        PersonalityTrait(dimension=TraitDimension.TONE, value=0.5, confidence=0.0, 
                        source="inferred", source_fact_id=None),
        PersonalityTrait(dimension=TraitDimension.VERBOSITY, value=0.5, confidence=0.0,
                        source="inferred", source_fact_id=None),
        PersonalityTrait(dimension=TraitDimension.FORMALITY, value=0.5, confidence=0.0,
                        source="inferred", source_fact_id=None),
        PersonalityTrait(dimension=TraitDimension.HUMOR, value=0.5, confidence=0.0,
                        source="inferred", source_fact_id=None),
        PersonalityTrait(dimension=TraitDimension.DIRECTNESS, value=0.5, confidence=0.0,
                        source="inferred", source_fact_id=None),
        PersonalityTrait(dimension=TraitDimension.CONFIDENCE, value=0.5, confidence=0.0,
                        source="inferred", source_fact_id=None),
    ],
    derived_at=datetime.utcnow(),
    source_traits_count=0,
    is_default=True
)
```

**PersonalityService API:**

```python
class PersonalityService:
    """
    Service for personality trait management.
    
    Backs PersonalityModule functionality.
    Uses MemoryService to read/write personality facts.
    """

    async def get_profile(
        self,
        session_id: UUID | None = None
    ) -> PersonalityProfile:
        """
        Get personality profile.
        
        Merges:
        1. Learned traits from Memory (long-term)
        2. Session-specific overrides (short-term)
        3. Default profile (if no learning)
        """
        ...

    async def get_system_prompt_context(
        self,
        session_id: UUID | None = None
    ) -> str:
        """
        Generate personality context for system prompt.
        
        Formats traits into natural language for LLM.
        """
        # Example output:
        # "The user prefers concise responses (0.8 confidence).
        #  They appreciate directness over sugarcoating.
        #  Default tone is fine, but humor is welcome."
        ...

    async def record_feedback(
        self,
        feedback: PersonalityFeedback
    ) -> list[Fact]:
        """
        Record explicit or implicit personality feedback.
        
        Explicit: User says "that was too harsh"
        Implicit: User consistently uses short responses
        
        Returns facts to be stored in Memory.
        """
        ...

    async def merge_with_learning(
        self,
        learned_traits: list[PersonalityTrait]
    ) -> PersonalityProfile:
        """
        Merge newly learned traits with existing profile.
        
        Uses confidence-weighted averaging.
        High-confidence learning overrides low-confidence existing.
        """
        ...
```

**Feedback Types:**

```python
class PersonalityFeedback(BaseModel):
    """User feedback about personality."""
    type: Literal["explicit", "implicit"]
    session_id: UUID
    timestamp: datetime
    details: FeedbackDetails

class FeedbackDetails(UnionBaseModel):
    """Discriminated union of feedback types."""
    # Explicit feedback
    correction: ExplicitCorrection | None
    # Implicit feedback
    communication_style: CommunicationStyle | None

class ExplicitCorrection(BaseModel):
    """User explicitly corrects personality."""
    dimension: TraitDimension
    direction: Literal["too_low", "too_high"]
    context: str | None              # "in your last response"

class CommunicationStyle(BaseModel):
    """Inferred from user behavior."""
    avg_message_length: float         # chars
    uses_questions: bool
    uses_emoji: bool
    formality_indicator: float
```

**System Prompt Integration:**

```python
# How personality context flows into system prompt

SYSTEM_PROMPT_TEMPLATE = """
You are Cortex, a personal AI assistant.

PERSONALITY CONTEXT:
{personality_context}

CURRENT CONTEXT:
- Time: {current_time}
- Location: {location}
- Activity: {activity}

USER'S KNOWN FACTS:
{facts_summary}

CONVERSATION HISTORY:
{conversation_history}

AVAILABLE TOOLS:
{tool_schemas}
"""

# Example personality_context output:
"""
The user prefers:
- Tone: Professional but approachable (confidence: 0.7)
- Verbosity: Concise responses preferred (confidence: 0.9)
- Directness: Blunt is fine, don't sugarcoat (confidence: 0.6)
- Humor: Light humor welcome (confidence: 0.5)
- Formality: Casual tone (confidence: 0.8)

Learned from: 23 interactions over 2 weeks.
"""
```

**Trait → System Prompt Mapping:**

| Trait | Value Range | Prompt Effect |
|-------|-------------|---------------|
| `tone` | 0.0-0.3 | Include occasional sarcasm hints |
| `tone` | 0.7-1.0 | Maintain serious, professional tone |
| `verbosity` | 0.0-0.3 | Keep responses under 2 sentences |
| `verbosity` | 0.7-1.0 | Provide detailed explanations |
| `formality` | 0.0-0.3 | Use contractions, casual language |
| `formality` | 0.7-1.0 | Use formal language, proper titles |
| `humor` | 0.0-0.3 | Avoid jokes, be matter-of-fact |
| `humor` | 0.7-1.0 | Include appropriate humor |
| `directness` | 0.0-0.3 | Use diplomatic language |
| `directness` | 0.7-1.0 | Be direct, state conclusions first |

**Learning Sources:**

| Source | How | Traits Affected |
|--------|-----|-----------------|
| Explicit correction | User says "too sarcastic" | tone |
| Response length | User consistently short | verbosity |
| Language formality | User uses "you're" vs "you are" | formality |
| Emoji usage | User uses 😄 vs 🙂 | humor |
| Feedback style | User says "just do it" | directness |

**Quota Reservation:**

The system prompt reserves a fixed section for personality context:

```
┌─────────────────────────────────────────────────────────────────┐
│                      SYSTEM PROMPT                               │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ ROLE: You are Cortex, a personal AI assistant.          │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                   │
│                              ▼                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ PERSONA RESERVED QUOTA (max 500 tokens)                 │   │
│  │                                                          │   │
│  │ The user prefers concise, direct responses.             │   │
│  │ Humor is welcome. Formality is casual.                   │   │
│  │ Confidence level: moderate (don't make definitive       │   │
│  │ claims about uncertain topics).                          │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                   │
│                              ▼                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ DYNAMIC CONTEXT (adapts to conversation)                │   │
│  │                                                          │   │
│  │ - Current time, location, activity                       │   │
│  │ - Relevant facts                                        │   │
│  │ - Tool schemas                                          │   │
│  │ - Conversation history                                  │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

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

> Task orchestration via the Agentic Loop. Runs the core reasoning cycle for both chat and goals.

| Aspect | Decision |
|--------|----------|
| Subscribes to | `goal.created`, `recommendation.executed` |
| Emits | `goal.status`, `goal.completed`, `goal.failed`, `module.spawn` |
| Owns LLM | **Yes** — powers the Agentic Loop |
| State | Active goals, spawned processes |

**Responsibilities:**
- Run the Agentic Loop for chat and goal execution
- Receive goals from Interaction Module
- Break down goals into sub-tasks
- Spawn workers/sub-processes as needed
- Track goal progress → emit `goal.status` events
- Coordinate multiple concurrent goals
- Manage context window (conversation truncation)

**Agentic Loop:**
The Agentic Loop is the heart of Cortex. It implements the Think → Act → Observe → Respond cycle.

```
┌─────────────────────────────────────────────────────────────────┐
│                      AGENTIC LOOP                               │
│                                                                  │
│  ┌──────────────┐     ┌─────────────┐                          │
│  │   CONTEXT    │────►│   THINK     │                          │
│  │   BUILDER    │     │   (LLM)     │                          │
│  └──────────────┘     └──────┬──────┘                          │
│                               │                                  │
│              ┌────────────────┼────────────────┐                 │
│              │                │                │                 │
│              ▼                ▼                ▼                 │
│    ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│    │   RESPOND    │  │    EXECUTE   │  │   CREATE     │        │
│    │   (done)     │  │    TOOLS     │  │   SUB-GOAL   │        │
│    └──────────────┘  └──────┬───────┘  └──────────────┘        │
│                              │                                   │
│                              ▼                                   │
│                       ┌──────────────┐                          │
│                       │   OBSERVE    │                          │
│                       │   (results)  │                          │
│                       └──────────────┘                          │
│                              │                                   │
│                              └───────────────────────────────────┘
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**Two Operating Modes:**

| Mode | Purpose | Characteristics |
|------|---------|----------------|
| **Chat** | Interactive conversation | Single session, immediate response, optional tools |
| **Goal** | Background tasks | Long-running, multi-step, progress tracking |

**Loop Components:**

| Component | Purpose |
|-----------|---------|
| **Context Builder** | Assembles context from session, memory, tools, personality |
| **Reasoner** | LLM decision-making: respond, execute tools, or create sub-goal |
| **Executor** | Tool execution with error handling and circuit breaker |
| **Conversation Manager** | Context window management, message truncation |

**Context Sources:**

| Source | Provides |
|--------|----------|
| Session | Conversation history (last N messages) |
| Memory | Relevant facts about user, current context |
| Tools | Available tools with schemas |
| Personality | User's learned preferences |
| Minions | Current location, activity (ambient) |

**Safety Limits:**

| Limit | Value | Purpose |
|-------|-------|---------|
| Chat max iterations | 20 | Prevent infinite loops |
| Goal max iterations | 100 | Allow complex tasks |
| Max tool errors | 3 | Stop on repeated failures |
| Context window | ~128K tokens | LLM context limit |

**Event Emissions:**

Loop progress is not published to the event bus. It is exposed to the caller via the `LoopEvent` streaming seam (ADR-0002): `stream_chat()` yields `LoopEvent` instances (see `src/cortex/agentic/events.py`) whose wire names are `thinking`, `text`, `tool_start`, `tool_done`, `done`, `error`.

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
| Interaction | No (thin interface) | API gateway, session management, response rendering |
| Execution | **Yes** | **Agentic Loop (Think → Act → Respond)** |
| Memory | Yes | Fact extraction, knowledge synthesis |
| Learning | Yes | Pattern analysis, preference inference |
| Tool Ecosystem | No | Tool execution (deterministic) |

**Note:** Interaction Module is a "thin interface" that handles I/O. It calls the Execution Module's Agentic Loop for reasoning. This separation ensures chat and goals share the same reasoning engine.

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

**On crash recovery:** Execution Module reads in-flight goals from DB on startup and resumes them with a direct `resume_in_flight()` call — no `goal.resumed` event (ADR-0004).

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

### Circuit Breaker

Every module that calls external services (LLM, database, tools) uses a circuit breaker pattern.

```
States: CLOSED → OPEN → HALF_OPEN → CLOSED

CLOSED: Normal operation, calls pass through
OPEN: Failure threshold exceeded, calls fail fast
HALF_OPEN: Testing if service recovered
```

| Parameter | Value | Description |
|-----------|-------|-------------|
| Failure threshold | 5 failures in 60s | Open circuit after 5 failures in 60 seconds |
| Open duration | 30s | Time circuit stays open before testing recovery |
| Half-open success threshold | 3 successes | Number of successes needed to close circuit |

**Implementation:** Circuit breaker wraps all external service calls per module. When circuit is OPEN, calls fail immediately with `CircuitOpenError` rather than waiting.

### Timeout Strategy

| Call Type | Default Timeout | Configurable |
|-----------|-----------------|--------------|
| LLM chat | 30s | Yes |
| Tool execution | 60s | Per-tool |
| Database | 10s | Yes |

On timeout: Retry once, then surface error to responsible module.

### Queue Depth Limits

Each module has a queue with max depth. When queue is full:
- **Blocking** — publisher waits until space available
- Prevents unbounded queue growth
- Backpressure propagates upstream

### Cascade Failure Prevention

When a module fails:
1. Module marked as `unhealthy`
2. Health checks detect and report
3. Other modules continue operating
4. Failed module requires manual intervention to recover (no auto-restart)

**Health check endpoint:** Each module exposes `/health` returning `{status: "healthy"|"unhealthy", last_event_at: timestamp}`

---

## Session Lifecycle

```
created → active → idle → ended
```

**States:**

| State | Description |
|-------|-------------|
| `created` | Session initialized, no messages yet |
| `active` | User actively interacting |
| `idle` | No activity for 5 minutes |
| `ended` | Explicitly ended or timed out |

**Transitions:**

| From | To | Trigger |
|------|-----|---------|
| created | active | First message received |
| active | idle | No activity for 5 minutes |
| idle | active | User sends message (resume) |
| idle | ended | Idle timeout exceeded (30 minutes) |
| active | ended | Explicit "end session" or application shutdown |

**Session Rules:**
- Single active session at a time (multi-session support deferred to future)
- All session data archived on end (conversation history, state, metadata)
- Archived sessions can be resumed while in `idle` state
- Session data retained indefinitely for learning and context

**Session Schema:**
```python
Session {
    id: UUID
    state: created | active | idle | ended
    created_at: datetime
    last_activity_at: datetime
    ended_at: datetime | null
    conversation_history: list[Message]
    metadata: dict
}
```

---

## Persistence

| Store | Technology | Purpose |
|-------|------------|---------|
| Sessions | SQLite (v1) → Postgres | Conversation history, session metadata |
| Facts | Postgres | User knowledge base (mutable + immutable), concepts/derived facts |
| Patterns | Postgres | Learned behavioral patterns |
| Preferences | Postgres | Inferred user preferences |
| Tool Registry | Postgres / Config YAML | Available tools and schemas |
| Recommendations | Postgres | Recommendation history (feedback loop) |

**Event bus (asyncio Queue)** is for real-time coordination, NOT persistence. All state survives restarts via Postgres.

---

## Persistence Models (v1)

> Design completed: 2026-04-21

### FactType Enum

```python
class FactType(str, Enum):
    PREFERENCE = "preference"   # learned or stated user preferences
    BEHAVIOR = "behavior"      # observed behavioral patterns
    KNOWLEDGE = "knowledge"    # facts about the world/user
    CONTEXT = "context"         # current state (time, place, activity)
```

### Base Payload (shared fields)

```python
class BaseFactPayload(BaseModel):
    source: str                              # freeform: "location_minion", "user_message", etc.
    event_timestamp: datetime                 # when the underlying event occurred
    minion_id: str | None = None              # if from minion
```

### Typed Fact Payloads

**LocationPayload** (from `location` event)

```python
class LocationFactPayload(BaseFactPayload):
    latitude: float
    longitude: float
    accuracy: float                          # meters
```

**PlacePayload** (derived from location clustering)

```python
class PlaceFactPayload(BaseFactPayload):
    name: str                                 # e.g., "Office", "Home"
    address: str | None
    category: str                            # "home" | "work" | "restaurant" | etc.
    is_significant: bool = False              # learned significance
```

**PreferencePayload** (from user messages / explicit corrections)

```python
class PreferenceFactPayload(BaseFactPayload):
    trait: str                               # "tone", "verbosity", "directness", etc.
    value: Any                               # typed value appropriate to trait
    source_type: str                         # "explicit" | "inferred"
```

**KnowledgePayload** (facts about user/world)

```python
class KnowledgeFactPayload(BaseFactPayload):
    subject: str                             # "user", "person_X", "project_Y"
    predicate: str                           # "works_at", "lives_in", "owns"
    object: Any                              # typed value
```

**BehaviorPayload** (observed patterns)

```python
class BehaviorFactPayload(BaseFactPayload):
    action: str                              # what was observed
    frequency: str | None                    # "daily", "weekly", "rarely"
    context: str | None                       # "when at home", "on weekdays"
```

**ContextPayload** (current state)

```python
class ContextFactPayload(BaseFactPayload):
    dimension: str                           # "time" | "location" | "activity"
    value: Any                               # typed value
```

### Fact Model

```python
class Fact(BaseModel):
    id: UUID
    type: FactType                            # category enum
    payload: FactPayload                      # discriminated union
    symbolic_repr: str                        # e.g., "lives_in(user, Rome)"
    natural_lang_repr: str                    # e.g., "I live in Rome"
    confidence: float                         # 0.0-1.0
    created_at: datetime
    retracted_at: datetime | None = None      # null = active

    # Hierarchy tracking
    layer: int = 0                            # 0=hot, n=cold
    access_count: int = 0
    last_accessed_at: datetime | None = None
```

### Session / Message Models

```python
class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"

class Message(BaseModel):
    id: UUID
    role: MessageRole
    content: str
    tool_calls: list[dict] | None = None     # raw tool call objects
    created_at: datetime

class SessionState(str, Enum):
    CREATED = "created"
    ACTIVE = "active"
    IDLE = "idle"
    ENDED = "ended"

class Session(BaseModel):
    id: UUID
    state: SessionState
    created_at: datetime
    last_activity_at: datetime
    ended_at: datetime | None = None
    conversation_history: list[Message]
    metadata: dict = {}
```

### Design Notes

| Aspect | Decision |
|--------|----------|
| Fact category | Controlled vocab (`FactType` enum) — the canonical way to reference facts |
| Payload typing | Discriminated union keyed on `type` field — enables type-safe payload access |
| Source field | Freeform string for flexibility — "location_minion", "user_message", etc. |
| Event metadata | All payloads embed originating event timestamp and minion_id for full traceability |
| Storage | SQLite v1, Postgres v2 |
| Deferred | Pattern, Preference, Recommendation models (v2) |

**Status:** ✅ Design agreed

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

> Shipped in #17. Protocol: SSE-only. Endpoint: `POST /chat/stream`.

### Decision: SSE-Only

| Decision | Rationale |
|----------|-----------|
| Protocol | Server-Sent Events (SSE) |
| Scope | v1 streaming — text, thinking, tool events |
| Complexity | Low — HTTP-native, no WebSocket overhead |
| Mobile | Native EventSource support on iOS/Android |

**Future (v2):** Add WebSocket only if bidirectional needs emerge.

---

### Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                       STREAMING FLOW                             │
│                                                                  │
│  Client                     API (chat_stream)               Loop │
│  ──────                     ─────────────────               ──── │
│    │                            │                              │ │
│    │ POST /chat/stream          │                              │ │
│    │ { message, session_id }    │                              │ │
│    │───────────────────────────►│                              │ │
│    │                            │ resolve session              │ │
│    │                            │ (missing → HTTP 404,         │ │
│    │                            │  absent → create)            │ │
│    │                            │                              │ │
│    │                            │ async for stream_chat()      │ │
│    │                            │─────────────────────────────►│ │
│    │                            │◄─ LoopEvent ───────────────  │ │
│    │                            │match event → SSE frame       │ │
│    │                            │(event_type IS the wire name) │ │
│    │◄─ SSE: thinking ────────   │                              │ │
│    │◄─ SSE: text ────────────   │                              │ │
│    │◄─ SSE: tool_start ──────   │                              │ │
│    │◄─ SSE: tool_done ───────   │                              │ │
│    │◄─ SSE: done ────────────   │                              │ │
│    │◄─ SSE: error ───────────   │ (on ErrorEvent / exception)  │ │
│    │                            │                              │ │
│    │ (connection closes)        │                              │ │
│    ◄─────────────────────────── │                              │ │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

### API Endpoint

`chat_stream` (route `POST /stream` on the `/chat` router) consumes the same
`ChatRequest` as the non-streaming endpoint and returns a `StreamingResponse`
that iterates `execution_module.stream_chat(...)` — a transparent passthrough
to `AgentLoop.stream_chat()` (no `MaxIterationsError` swallowing, unlike
`run_chat()`'s fallback). Session resolution happens before the stream starts,
then each `LoopEvent` is matched to an SSE frame:

```python
@router.post("/stream")          # router prefix "/chat" → POST /chat/stream
async def chat_stream(
    request: ChatRequest,
    key: str = Depends(get_api_key),
    interaction_service: InteractionService = Depends(get_interaction_service),
    execution_module: ExecutionModule = Depends(get_execution_module),
) -> StreamingResponse:
    # Session resolution before the stream (see below)...
    # max_iterations = request.max_iterations or 20

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            async for event in execution_module.stream_chat(
                session_id=session_id,
                user_message=request.message,
                max_iterations=max_iterations,
            ):
                match event:
                    case ThinkingEvent():
                        yield _sse_event("thinking", {"message": event.message})
                    case TextDeltaEvent():
                        yield _sse_event("text", {"delta": event.delta})
                    case ToolStartEvent():
                        yield _sse_event("tool_start", {
                            "tool_name": event.tool_name,
                            "tool_call_id": event.tool_call_id,
                        })
                    case ToolResultEvent():
                        yield _sse_event("tool_done", {
                            "tool_name": event.tool_name,
                            "tool_call_id": event.tool_call_id,
                            "success": event.success,
                            "output": event.output,
                            "error": event.error,
                            "execution_time_ms": event.execution_time_ms,
                        })
                    case ResponseDoneEvent():
                        yield _sse_event("done", {
                            "final_message": event.message,
                            "tool_calls": event.tools_used,
                            "iterations": event.iterations,
                        })
                    case ErrorEvent():
                        yield _sse_event("error", {"error": event.error, "code": event.code})
                        return
                    case _:
                        logger.warning(f"Unhandled loop event: {type(event).__name__}")
        except Exception as exc:
            logger.exception("Unexpected error streaming chat events")
            yield _sse_event("error", {"error": str(exc), "code": None})
            raise

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
```

`_sse_event(event_type, data)` formats each frame as
`event: {event_type}\ndata: {json.dumps(data)}\n\n`.

---

### Wire Protocol (SSE events)

One vocabulary: a `LoopEvent`'s `event_type` IS the SSE wire name — the mapping
is an explicit minimal field mapping, not `to_dict()`. `session_id` and
`duration_ms` are never on the wire — a per-request SSE connection needs no
session attribution, and nothing measures duration.

| SSE event | Payload | Source event |
|-----------|---------|--------------|
| `thinking` | `{message}` | `ThinkingEvent` |
| `text` | `{delta}` | `TextDeltaEvent` |
| `tool_start` | `{tool_name, tool_call_id}` | `ToolStartEvent` |
| `tool_done` | `{tool_name, tool_call_id, success, output, error, execution_time_ms}` | `ToolResultEvent` |
| `done` | `{final_message, tool_calls, iterations}` | `ResponseDoneEvent` (renames `message`→`final_message`, `tools_used`→`tool_calls`) |
| `error` | `{error, code}` | `ErrorEvent` (`code` is `"max_iterations"` or `null`) |

```
event: thinking
data: {"message": "Let me check your calendar..."}

event: text
data: {"delta": "You have a meeting at 2pm with John."}

event: tool_start
data: {"tool_name": "shell", "tool_call_id": "call_01"}

event: tool_done
data: {"tool_name": "shell", "tool_call_id": "call_01", "success": true, "output": "On branch main", "error": null, "execution_time_ms": 234}

event: done
data: {"final_message": "You have a meeting at 2pm with John.", "tool_calls": ["shell"], "iterations": 2}

event: error
data: {"error": "Max iterations reached", "code": "max_iterations"}
```

---

### Session Resolution

Session resolution happens before the stream starts, under the same policy as
the non-streaming endpoint:

- A provided `session_id` that does not exist → HTTP 404 (never an in-stream
  `error` frame).
- An absent `session_id` → a new session is created.

The stream itself only iterates `execution_module.stream_chat()`; no session
work happens inside the generator.

---

### Error Policy

- **Expected `ErrorEvent`:** the adapter yields the `error` frame and returns.
  The loop's yield-then-reraise contract stays silent on the server, so
  expected conditions like `max_iterations` produce no traceback.
- **Unexpected exceptions** in the adapter (bugs, serialization): yield an
  `error{code: null}` frame, log, then re-raise — never silent.
- **Unknown event types:** logged and skipped from the wire — dropped, but not
  silent.

Consequence: streaming and non-streaming diverge on max-iterations — the stream
emits `error{code: "max_iterations"}` and ends, while non-streaming
`run_chat()` returns the friendly fallback message.

---

### v1 Scope

```
✅ Chat text streaming (SSE text event)
✅ Thinking indicator (SSE thinking event)
✅ Tool start/done events (SSE tool_start, tool_done)
✅ Done event with final message, tool calls, iterations
✅ Error frames (error{error, code}) for expected and unexpected failures

❌ Tool output streaming (tail -f style)
❌ Tool progress updates (steps/percentage)
❌ WebSocket bidirectional
❌ Stream reconnection/resume
❌ E2E encryption (not needed for local deployment)
```

---

### Client Example

```javascript
// Modern approach with Fetch + ReadableStream
const response = await fetch('/chat/stream', {
    method: 'POST',
    body: JSON.stringify({ message: 'Deploy the app' }),
    headers: { 'Content-Type': 'application/json' }
});

const reader = response.body.getReader();
const decoder = new TextDecoder();

// SSE parser
function parseSSELines(chunk) {
    const lines = chunk.split('\n');
    const events = [];
    let current = {};

    for (const line of lines) {
        if (line === '') {
            if (current.event && current.data) {
                events.push(current);
            }
            current = {};
        } else if (line.startsWith('event: ')) {
            current.event = line.slice(7);
        } else if (line.startsWith('data: ')) {
            current.data = line.slice(6);
        }
    }
    return events;
}

while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    const chunk = decoder.decode(value);
    parseSSELines(chunk).forEach(({ event, data }) => {
        const payload = JSON.parse(data);

        switch (event) {
            case 'thinking':
                showThinking(payload.message);
                break;
            case 'text':
                appendToResponse(payload.delta);
                break;
            case 'tool_start':
                showToolExecuting(payload.tool_name);
                break;
            case 'tool_done':
                showToolResult(payload.tool_name, payload.output, payload.error);
                break;
            case 'done':
                showStats(payload.iterations);
                break;
            case 'error':
                showError(payload.error, payload.code);
                break;
        }
    });
}
```

---

### Settled Questions

- **Protocol:** SSE-only (no WebSocket)
- **Tool output:** Not streamed (final summary in `tool_done.output`)
- **Tool progress:** Simple start/done events (no steps)
- **Client → Server:** Not needed for v1
- **Reconnection:** Not supported (new stream per request)
- **Backpressure:** No explicit buffering — `StreamingResponse` streams frames as the generator produces them; `X-Accel-Buffering: no` disables proxy buffering (nginx)

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
- **Agentic Loop:** Heart of Cortex — Think → Act → Observe → Respond cycle. Lives in Execution Module. Two modes: Chat (interactive) and Goal (background). Safety limits prevent infinite loops.
- **Interaction Module:** Thin interface — handles API gateway, session management, response rendering. Calls Execution Module's Agentic Loop for reasoning.
- **Input streams:** Two — Direct input (chat, tools, goals) and Sensory input (minions as organs)
- **Minions:** Organs that stream life data; phones, cards, laptops send events to Cortex via MQTT with API token auth
- **Minion transport:** MQTT 5.0 (battery efficient, persistent connections, QoS 1 delivery)
- **Minion auth:** API token via MQTT username/password (no mTLS for v1, trusted network assumption)
- **Minion broker:** Self-hosted Mosquitto in Docker (port 1883)
- **Minion events:** Full event schemas defined in [MINION_EVENTS.md](MINION_EVENTS.md) — location, activity, calendar, app_usage, call_log, payment, refund, screen_activity, application_focus, keyboard_activity, battery, network_status
- **Event bus:** In-memory asyncio Queue — all inter-module communication flows through events
- **Event schema:** Versioned Pydantic models for core events; BaseEvent + per-type payloads
- **Salience mechanism:** Events carry salience score (0.0-1.0); modules filter by threshold
- **Event naming:** Session in metadata only, no wildcard subtypes in event types
- **Module interfaces:** Pure events for all communication; each module subscribes/publishes defined events
- **Fact storage:** Postgres — facts (mutable/immutable), concepts (derived), logic engine (PyDatalog)
- **Fact model:** Symbolic representation + natural language, hierarchy tree with frequency-adjusted promotion, explicit retraction via `retracted_at`
- **Concept derivation:** LLM proposes with proof chain, logic engine validates, cascade invalidation on source change
- **Persistence:** Postgres for all state; event bus for real-time coordination only
- **LLM ownership:** Execution Module (Agentic Loop), Memory, Learning each have their own LLM client
- **LLM resource management:** Priority queue — Execution > Memory > Learning
- **Tool correlation:** `correlation_id` in tool.request/result payloads
- **Goal lifecycle:** goal.created → goal.status → goal.completed / goal.failed (resume is a direct startup call per ADR-0004, not an event)
- **Module lifecycle:** Health checks via `/health` endpoint, orchestrator polls
- **Tool extensibility:** Registry-based — register tools in DB, no core code changes
- **Dynamic spawning:** Yes — Execution Module can spawn workers for complex tasks
- **Learning scope:** Both preferences AND knowledge; proactive recommendations powered by minion data
- **Memory role:** Proactive transformer — watches all events (user + minion), extracts facts autonomously. Ambient context for Agentic Loop.
- **MemoryService API:** Explicit service interface for querying/storing facts. All modules use this API, not raw SQL. Defined in ARCHITECTURE.md.
- **PersonalityModule:** Manages learned personality traits. Traits are stored as facts in Memory (type=`preference`). PersonalityService provides context for system prompt injection with reserved quota (max 500 tokens).
- **Minion security (v1):** Plain MQTT, no TLS, API token auth via MQTT username/password. Runs on trusted local network.
- **Streaming:** SSE-only. Events: thinking, text, tool_start, tool_done, done. Non-blocking buffer with drop-oldest policy. No reconnection/resume in v1.
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
├── conversation.message             # agent responses — extract facts
└── minion.* (all types)             # sensory data — extract facts

Service API:
└── MemoryService                    # exposed to other modules for querying

Database:
└── Postgres (facts table)          # facts stored via FactStore
```

**Internal Components:**
```python
MemoryService             # Service API (query, store, search)
FactStore                # Postgres client for facts/concepts
FactExtractor           # LLM client — extracts structured facts from text
LogicEngine             # PyDatalog — validates LLM reasoning, checks consistency
HierarchyManager        # manages frequency-adjusted fact hierarchy (hot/warm/cold)
```

**Public API (MemoryService):**
```python
get_relevant(query, limit, session_id, fact_types) -> list[Fact]
get_context(dimensions) -> dict  # ambient context (time, location, activity)
get_personality_context() -> PersonalityProfile
search(query, filters) -> list[Fact]
store_fact(fact) -> Fact
retract_fact(fact_id) -> None
propose_concept(derivation) -> Concept | Rejection
```

**Fact categories:** `preference`, `behavior`, `knowledge`, `context`

---

### Personality Module

**Purpose:** Manage learned personality traits and provide context for response formatting.

```
Subscriptions:
├── user.feedback                    # explicit personality corrections
├── preference.learned               # newly learned preferences
└── conversation.ended              # analyze conversation for implicit feedback

Service API:
└── PersonalityService               # provides personality context for prompts

Dependencies:
└── MemoryService                     # reads/writes personality facts
```

**Internal Components:**
```python
PersonalityService        # public API for personality management
TraitAggregator          # merges traits from Memory
FeedbackProcessor        # handles explicit and implicit feedback
SystemPromptBuilder      # formats traits into natural language
```

**Public API (PersonalityService):**
```python
get_profile(session_id) -> PersonalityProfile
get_system_prompt_context(session_id) -> str  # for system prompt injection
record_feedback(feedback) -> list[Fact]
```

**Trait Dimensions:** `tone`, `verbosity`, `formality`, `humor`, `directness`, `confidence`

**System Prompt Quota:** Max 500 tokens reserved for personality context.

---

### Learning Module (Stub)

**Purpose:** Pattern detection, preference inference, proactive recommendations. (Stub for MVP — stores raw events, full analysis deferred.)

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
└── module.spawn                     # spawn a worker process
```

**Internal Components:**
```python
GoalOrchestrator         # manages goal lifecycle, breaks into sub-tasks
WorkerSpawner            # dynamically spawns worker processes
ProgressTracker          # emits goal.status updates
```

**Crash recovery:** On startup, reads in-flight goals from DB and resumes them via a direct `resume_in_flight()` call — no `goal.resumed` event (ADR-0004).

---

### Interface Summary

| Module | Input Events | Output Events | Service API |
|--------|-------------|---------------|------------|
| Interaction | `user.message`, `recommendation.generated` | `conversation.message`, `goal.created` | `InteractionService` |
| Memory | `user.message`, `conversation.message`, `minion.*` | (writes via `MemoryService`) | `MemoryService` (public) + `FactStore`, `FactExtractor`, `LogicEngine`, `HierarchyManager` (internal) |
| Personality | `user.feedback`, `preference.learned`, `conversation.ended` | (writes via `MemoryService`) | `PersonalityService` |
| Learning | `user.message`, `conversation.message`, `goal.*`, `recommendation.executed` | `pattern.detected`, `preference.learned`, `recommendation.generated` | `PatternAnalyzer`, `PreferenceEngine`, `Recommender` |
| Tool Ecosystem | `tool.request` | `tool.result` | `ToolRegistry`, `ToolExecutor` |
| Execution | `goal.created`, `recommendation.executed` | `goal.status`, `goal.completed`, `goal.failed`, `module.spawn` | `GoalOrchestrator`, `WorkerSpawner` |

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
│       │   ├── routes.py             # /chat, /chat/stream, /health endpoints
│       │   ├── streaming.py          # StreamManager, SSE formatting
│       │   └── models.py             # Request/Response models
│       │
│       ├── interaction/              # Interaction Module
│       │   ├── __init__.py
│       │   ├── service.py
│       │   └── renderer.py
│       │
│       ├── memory/                   # Memory Module
│       │   ├── __init__.py
│       │   ├── service.py            # MemoryService (public API)
│       │   ├── fact_store.py         # Postgres client for facts/concepts
│       │   ├── extractor.py          # LLM fact extraction
│       │   ├── logic_engine.py       # PyDatalog validation
│       │   ├── hierarchy.py          # Frequency-adjusted fact hierarchy
│       │   └── models.py             # Fact, Concept, SearchFilters models
│       │
│       ├── personality/              # Personality Module
│       │   ├── __init__.py
│       │   ├── service.py            # PersonalityService (public API)
│       │   ├── aggregator.py         # Trait aggregation from Memory
│       │   ├── feedback.py          # Explicit/implicit feedback processing
│       │   ├── prompt_builder.py    # System prompt context generation
│       │   └── models.py             # PersonalityTrait, PersonalityProfile models
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
│           └── bus.py                # In-memory asyncio Queue event bus
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
| PostgreSQL | Sessions, facts (mutable/immutable + concepts), patterns, preferences, tool registry |

(Redis can be added later for event bus resilience when production needs are understood.)

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
      - postgres
    environment:
      - POSTGRES_HOST=postgres

  interaction:
    build: ./src/cortex/interaction
    container_name: cortex-interaction
    networks:
      - cortex-net
    depends_on:
      - postgres
    environment:
      - POSTGRES_HOST=postgres
      - LLM_PROVIDER=${LLM_PROVIDER}

  memory:
    build: ./src/cortex/memory
    container_name: cortex-memory
    networks:
      - cortex-net
    depends_on:
      - postgres
    environment:
      - POSTGRES_HOST=postgres

  learning:
    build: ./src/cortex/learning
    container_name: cortex-learning
    networks:
      - cortex-net
    depends_on:
      - postgres
    environment:
      - POSTGRES_HOST=postgres
      - LLM_PROVIDER=${LLM_PROVIDER}

  tools:
    build: ./src/cortex/tools
    container_name: cortex-tools
    networks:
      - cortex-net
    depends_on:
      - postgres
    environment:
      - POSTGRES_HOST=postgres

  execution:
    build: ./src/cortex/execution
    container_name: cortex-execution
    networks:
      - cortex-net
    depends_on:
      - postgres
    environment:
      - POSTGRES_HOST=postgres

  postgres:
    image: postgres:15
    container_name: cortex-postgres
    networks:
      - cortex-net
    environment:
      - POSTGRES_USER=cortex
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - POSTGRES_DB=cortex
    volumes:
      - postgres_data:/var/lib/postgresql/data

volumes:
  postgres_data:

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
6. [x] Define Pydantic models for persistence (sessions, facts, patterns)
7. [x] Design Minion protocol (communication, encryption, transport)
8. [x] Define minion event schemas (location, payment, activity, etc.)

---

## Minions

> Design complete — see [MINION_PROTOCOL.md](MINION_PROTOCOL.md) for full details.

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
│  │ Data        │  │ Filter &    │  │ MQTT Client         │ │
│  │ Collectors  │→ │ Normalizer  │→ │ (API token auth)    │ │
│  │ (GPS, API)  │  │ (debounce,  │  │                     │ │
│  │             │  │  dedup)     │  │                     │ │
│  └─────────────┘  └─────────────┘  └──────────┬──────────┘ │
└───────────────────────────────────────────────┼─────────────┘
                                                │ MQTT (plain)
                                                ▼
                                      ┌─────────────────┐
                                      │   MQTT Broker  │
                                      │  (Mosquitto)   │
                                      │   Port 1883     │
                                      └────────┬────────┘
                                               │
                                      ┌────────┴────────┐
                                      │        ▼        │
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
| Transport | MQTT 5.0 (plain, no TLS for v1) |
| Authentication | API token via MQTT username/password |
| Encryption | None (v1 runs on trusted local network) |
| QoS | QoS 1 (at-least-once delivery) |
| Data ownership | All data stays local (user's devices + user's Cortex instance) |
| Cortex role | Process only — no commands to minions (v1) |
| Minion autonomy | Minions decide what/when to send |

### Authentication Model

```
┌─────────────────────────────────────────────────────────────┐
│                    v1 Security Model                        │
│                                                              │
│  ┌──────────────────┐         ┌──────────────────┐         │
│  │     Minion       │         │   MQTT Broker    │         │
│  │                  │         │                  │         │
│  │  username:       │         │  passwd.conf     │         │
│  │    minion_<id>   │────────►│  (bcrypt hash)   │         │
│  │                  │         │                  │         │
│  │  password:       │         └──────────────────┘         │
│  │    <API_TOKEN>   │                                      │
│  └──────────────────┘                                      │
│                                                              │
│  Token generated by: Cortex Admin UI                       │
│  Token stored in: Minion config file                       │
│  Token revocation: Delete from passwd.conf                │
└─────────────────────────────────────────────────────────────┘
```

**v1 assumption:** All minions run on the same trusted network (home WiFi). No encryption needed within trust boundary.

**Future (v2):** Add TLS when minions connect over untrusted networks.

### MQTT Topics

```
cortex/minions/<minion_id>/
├── register              # Registration (QoS 1)
├── register/response      # Response
├── events                 # Event batch (QoS 1)
├── heartbeat              # Status updates (QoS 0)
└── commands/              # Cortex → Minion (future)
    └── config             # Config push
```

### Provisioning Flow

```
1. User opens Cortex Admin UI
2. UI generates random API token (UUID v4)
3. User copies token to minion config file
4. Minion connects with username=minion_<id>, password=<token>
5. Broker verifies token against passwd.conf
6. Minion publishes registration event
7. Cortex confirms registration, sends config
```

### Event Flow

1. Minion collects raw data (GPS, payment, etc.)
2. Minion filters/normalizes (debounce location, deduplicate payments)
3. Minion batches events (configurable size/interval)
4. Minion publishes to MQTT topic (QoS 1)
5. Broker delivers to Cortex MQTT client
6. Cortex emits events to event bus
7. Memory/Learning modules process automatically

### Offline Handling

- **Broker persistence:** Messages queued for up to 24 hours
- **Minion queue:** Local encrypted queue if disconnected
- **Reconnection:** Resume from last sequence, flush queue

### Minion Event Types

> Full schemas in [MINION_EVENTS.md](MINION_EVENTS.md)

| Event | Source | Key Payload | Emitted When |
|-------|--------|-------------|--------------|
| **Phone** | | | |
| `location` | GPS, network | lat/long, accuracy, speed, heading | GPS update (debounced: 100m or 60s) |
| `activity` | ActivityRecognition | in_vehicle, walking, running, still | Activity transition / 5 min periodic |
| `calendar` | Calendar API | title, start/end, attendees, location | Created/modified/deleted/reminder |
| `app_usage` | UsageStats | app, duration, usage_type | 15 min summary / app switch |
| `call_log` | CallLog | direction, duration, (anonymized) | Call ended |
| **Card** | | | |
| `payment` | Card reader | amount, merchant, MCC, location | Transaction auth/settlement |
| `refund` | Card reader | amount, original_tx_id, status | Refund initiated/completed |
| **Laptop** | | | |
| `screen_activity` | OS hooks | screen_on/off, window_title | Screen state / window change / idle |
| `application_focus` | OS hooks | app, duration, window_title | App switch / 5 min summary |
| `keyboard_activity` | OS hooks | keystrokes, clicks, scroll (aggregated) | 15 min summary |
| **Common** | | | |
| `battery` | System API | level, charging, health | Level change >5% / charging |
| `network_status` | System API | type, ssid, signal, vpn | Network change |

### Event Privacy Controls

| Event | Default | User Can Disable |
|-------|---------|------------------|
| `location` | ✅ On | Yes — precision reduction (city level) |
| `calendar` | ✅ On | Per-calendar permissions |
| `app_usage` | ⚠️ Whitelist | Must explicitly allow apps |
| `call_log` | ✅ Anonymized | Phone numbers never transmitted |
| `payment` | ✅ Full | Can reduce to category-only |
| `screen_activity` | ⚠️ App name only | Window titles disabled by default |
| `keyboard_activity` | ✅ Aggregated | Only counts, no raw keystrokes |

---

*Last updated: 2026-04-28*
