-- 010_evidence.sql
-- Evidence for the Bayesian fact-confidence engine (ADR-0013, issue #83).
-- One row per accepted observation; `facts.confidence` is the posterior over
-- the rows that are active for the fact's current value and mutability window.
-- Idempotent: guards make re-runs safe.

CREATE TABLE IF NOT EXISTS evidence (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fact_id UUID NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
    source_type VARCHAR(20) NOT NULL,
    source_id TEXT NOT NULL,
    value_hash TEXT NOT NULL,
    strength FLOAT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_evidence_fact_time ON evidence(fact_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_evidence_time ON evidence(observed_at);
