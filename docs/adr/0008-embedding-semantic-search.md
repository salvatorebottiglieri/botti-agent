# Embedding-based semantic fact retrieval with ILIKE fallback

Fact search used `ILIKE` substring matching on `symbolic_repr` and `natural_lang_repr`,
making semantic queries like "where do I work?" impossible — they never match
`symbolic_repr = "location.office"`.

We added pgvector-backed embedding search as a first stage (top 50 candidates via ANN),
followed by existing symbolic re-ranking (FactType filter, confidence threshold, recency
boost). Embeddings are generated eagerly at `store_fact()` time via the LLM's embedding
endpoint (`text-embedding-3-small`). An `embed()` method was added to `LLMClient`.

If pgvector is unavailable or the embedding API fails, the system falls back to ILIKE
search via the existing `MemoryContext.degraded_dimensions` mechanism.
