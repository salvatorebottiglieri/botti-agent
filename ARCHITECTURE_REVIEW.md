# Architecture Review — Problems to Address

> Honest review of Cortex architecture. Each item to be addressed before proceeding.
> Created: 2026-04-19

---

## Major Concerns

### 1. Over-engineering for MVP
**Problem:** Architecture delivers 5 modules, Redis, Postgres, Graph DB, 6 containers for what could start much simpler.

**What you want:** Personal assistant that learns patterns and delegates tasks.
**What architecture delivers:** Full event-driven microservices ecosystem.

**Resolution:** ✅ Resolved — Single Minion MVP

| Decision | Value |
|----------|-------|
| MVP type | Single Minion + simplified modules |
| Minion type | Location (Phone) |
| Minion sends | Raw lat/long + accuracy + minion_id |
| Frequency | Periodic (configurable N minutes) |
| Significant places | Cortex clusters raw data |
| Chat queries | All 4 supported in v1 |

**Key design decisions:**
1. Keep raw data for minions — the brain (Cortex) will do reverse geocoding
2. Periodic sending — the brain learns, not the organs
3. Cortex does significant place detection via clustering

**Modules for v1:** Interaction + Memory (no Learning yet)

---

### 2. Learning Module Scope is Unrealistic
**Problem:** Learning Module is expected to:
- Watch ALL events
- Extract behavioral patterns from "repeated behaviors"
- Infer preferences automatically
- Generate proactive recommendations
- Track feedback on recommendations

**What's missing:**
- How are patterns detected? (embeddings? rules? ML?)
- What does a pattern look like in the data model?
- When does learning run? (every event? batch? background?)
- What triggers a recommendation?

**Resolution:** ✅ Resolved — Human Brain Sleep Model

**Architecture model:**

```
Day Phase (Memory Module active):
  Raw events → Memory Module → Extracted raw facts (unconsolidated)

Night Phase (Memory + Learning collaborative "sleep"):
  Memory Module "cleans" raw facts:
    - Remove duplicates
    - Resolve contradictions
    - Consolidate similar facts
  ↓
  Cleaned facts stored separately (retainable for a period)

  Learning Module reads cleaned facts:
    ├── Rules (quick escalation) — obvious patterns immediately surfaced
    └── ML Clustering (consolidation) — subtle patterns via embedding + clustering
```

**Key decisions:**

| Aspect | Decision |
|--------|----------|
| Learning input | Only from Memory Module (not raw events) |
| Memory cleaning | Remove duplicates, resolve contradictions, consolidate |
| Raw facts after cleaning | Kept separately for a period |
| Learning trigger | User asleep (Location Minion detects no movement) OR user-triggered |
| Pattern detection | Rules (quick escalation) + ML Clustering (deep consolidation) |

---

### 3. No LLM Prompting Strategy
**Problem:** We designed the nervous system (events) but not the brain (prompts).

**What's missing:**
- What system prompt does the Interaction Module use?
- How is context assembled for a response?
- How does the LLM know about recent facts about the user?
- How does it decide to create a goal vs respond directly?

**Resolution:** ✅ Resolved — Thin Interface + Learned Personality

### Interaction Module Role
Thin interface between user and Cortex brain. Routes queries, formats responses using learned personality.

**Prompt Components:**

| Component | Source | Loaded |
|-----------|--------|--------|
| System prompt | Static | At startup |
| Personality context | Memory Module | Per query |
| Conversation history | Session | Per query |
| User query | Current message | Per query |

### System Prompt
```
You are a chat interface for a personal AI assistant.
Format responses according to the user's learned preferences.
Be clear, helpful, and adapt to the user's communication style.
```

### Personality Context Format
```json
{
  "user_personality": {
    "tone": 0.5,      // 0=sarcastic, 1=serious
    "verbosity": 0.5, // 0=concise, 1=detailed
    "formality": 0.5, // 0=casual, 1=formal
    "humor": 0.5,     // 0=dry, 1=enthusiastic
    "directness": 0.5, // 0=blunt, 1=tactful
    "confidence": 0.3  // learned confidence level
  }
}
```

### Learnable Traits
All — Tone, Verbosity, Formality, Humor, Directness

### Learning Mechanism
- **Feedback:** Explicit user corrections ("that was too harsh")
- **Observation:** User's own communication style shapes Cortex

### Default (Before Learning)
Neutral (0.5 on all traits)

### Query Routing Logic
| User Intent | Route To | Example |
|------------|----------|---------|
| Question | Memory | "Where do I work?" |
| Task delegation | Executor | "Deploy the app" |
| Simple chat | Interaction (direct) | "Hello" |
| Tool use | Tool Ecosystem | "Read file X" |

### Memory Module Analysis
Memory Module, NOT Interaction Module, does the reasoning when queried.
Interaction Module only formats the response using personality context from Memory.

---

### 4. LLM Contention — Memory + Learning Both Extract
**Problem:** Memory Module extracts facts via LLM. Learning Module watches all events via LLM. Both could hit the LLM provider simultaneously during high activity.

**What's missing:**
- No rate limiting defined beyond priority queue
- No batching strategy for fact extraction
- Memory + Learning could conflict on high-volume conversations

**Recommendation:** First, clarify if Memory and Learning are truly separate or if Memory feeds Learning. Then define batching/throttling strategy.

---

### 5. Graph DB Deferred is a Red Flag
**Problem:** "Facts stored in Graph DB (TBD)" is a major architectural decision left undefined.

**Affects:**
- How facts are queried
- How relationships are modeled
- Infrastructure choices
- Query patterns

**Recommendation:** Use simple Postgres table for facts MVP. Only introduce Graph DB when Postgres queries become insufficient.

---

### 6. Redis SPOF Despite "Fallback"
**Problem:** Fallback to DB-backed queue sounds good but:
- DB-backed queues are much slower than Redis pub/sub
- Detection logic for Redis failure not designed
- Replay logic when Redis recovers not designed

**Recommendation:** For MVP, use in-memory events (asyncio Queue). Add Redis when resilience is actually needed.

---

### 7. No Backpressure or Circuit Breaker
**Problem:** If LLM latency spikes:
- Interaction Module waits
- Memory Module queues
- Learning Module queues
- Everything backs up

**What's missing:**
- Circuit breaker for external service calls
- Timeout strategy per module
- Queue depth limits
- Cascade failure prevention

**Recommendation:** Every module that calls external services needs timeout + retry + circuit breaker logic.

---

## Specific Problems Summary

| # | Problem | Severity | Status |
|---|---------|----------|--------|
| 1 | MVP scope too large | High | ✅ Resolved — Single Minion MVP |
| 2 | Learning is undefined black box | High | ✅ Resolved — Human Brain Sleep Model |
| 3 | No prompting strategy | High | ✅ Resolved — Thin Interface + Learned Personality |
| 4 | LLM contention (Memory + Learning) | High | ✅ Resolved (by #2) — Learning consumes only Memory output |
| 5 | Graph DB deferred | Medium | ⏳ Pending |
| 6 | Redis fallback complex | Medium | ⏳ Pending |
| 7 | No backpressure | Medium | ⏳ Pending |
| 8 | 9 infrastructure components | Medium | ✅ Resolved (by #1) |
| 9 | No session lifecycle defined | Low | ⏳ Pending |

---

## Suggested Improvements

### For MVP (Phase 1)

1. **Single module to start** — Interaction Module + Tool Ecosystem only
2. **Define prompting first** — Write actual system prompts before infrastructure
3. **Facts = simple Postgres table** — Not Graph DB. Iterate when you understand query patterns.
4. **No Redis initially** — In-memory events for MVP
5. **Learning = passive storage** — Store raw conversations. Don't extract patterns yet.

### For Architecture (Phase 2+)

1. **Define learning algorithm** — Research/implement pattern detection before building Learning Module
2. **Add prompting section** — Interaction Module needs actual prompt engineering
3. **Specify Graph DB when needed** — Only when Postgres becomes insufficient
4. **Circuit breakers** — Every module that calls external services needs timeout + retry logic

---

## Bottom Line

This is a **v2 architecture** — designed for a system that's already proven its value and needs to scale. But there is no v1 yet.

**Recommended path:** Build a simple chat agent first:
- Takes user input
- Calls an LLM
- Executes tools
- Stores conversations in Postgres
- Done.

Then, when that works and actual needs are understood, split into modules. The river metaphor ("water flows, modules emerge") suggests organic growth — not upfront design.

---

## Next Steps (Ordered)

1. [ ] Simplify to MVP scope — single module + tool ecosystem
2. [ ] Define prompting strategy — system prompts, context assembly
3. [ ] Use Postgres for facts — defer Graph DB
4. [ ] No Redis — in-memory events for MVP
5. [ ] Define Learning as passive storage first
6. [ ] Add circuit breakers and backpressure
7. [ ] Add prompting section to ARCHITECTURE.md
8. [ ] Revisit module split when MVP works
