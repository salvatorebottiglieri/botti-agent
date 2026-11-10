-- 009_embeddings.sql
-- pgvector-backed semantic fact retrieval (ADR-0008, issue #31).
-- Idempotent: the extension, column and index guards make re-runs safe.

CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE facts ADD COLUMN IF NOT EXISTS embedding vector(1536);

-- ANN index for cosine-distance search over fact embeddings. lists = 100 is
-- the ADR-0008 starting point for the expected fact volume.
CREATE INDEX IF NOT EXISTS idx_facts_embedding
    ON facts USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- Serves the embedding backfill: the rows that still have no vector.
CREATE INDEX IF NOT EXISTS idx_facts_embedding_null
    ON facts(id) WHERE embedding IS NULL;
