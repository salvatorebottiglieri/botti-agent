# Split MemoryService into FactStore, ContextProvider, FactExtractor

The single `MemoryService` class held 9 responsibilities: fact CRUD, relevance scoring,
personality aggregation, ambient context, fact extraction from events, fact extraction from
text, concept building, the bundle seam (`get_memory_context`), and event-bus subscriptions.
It was a shallow module — callers had to know which of 15+ methods to use for each purpose.

We split it into three modules, each with a single interface:

- **FactStore** — pure CRUD over `FactRepository`. Owns dedup-on-write. No LLM, no event bus,
  no concept cascade.
- **ContextProvider** — the bundle seam (`get_memory_context`) plus individual dimension
  methods (personality, ambient) and cross-repository operations (cascade invalidation on retract,
  concept building). Consumed by `ContextBuilder` and `PersonalityService`.
- **FactExtractor** — ingestion pipeline: raw events and text → structured facts. Public
  `extract_from_event_type` replaces the private `_extract_location_facts` pattern that
  `MinionService` was calling across module boundaries.

Rejected alternative: keeping a single `MemoryService` with the same 15+ methods. The private
method call from `MinionService` to `_extract_location_facts` proved the encapsulation had
already broken — splitting was overdue, not premature.
