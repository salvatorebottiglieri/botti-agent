-- 003_facts.sql
-- Facts and concepts tables for memory module

CREATE TABLE IF NOT EXISTS facts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type VARCHAR(50) NOT NULL,
    mutability VARCHAR(20) NOT NULL DEFAULT 'mutable',
    symbolic_repr TEXT NOT NULL,
    natural_lang_repr TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}',
    confidence FLOAT NOT NULL DEFAULT 0.5,
    layer INTEGER NOT NULL DEFAULT 0,
    access_count INTEGER NOT NULL DEFAULT 0,
    last_accessed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    retracted_at TIMESTAMP WITH TIME ZONE
);

CREATE TABLE IF NOT EXISTS concepts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbolic_repr TEXT NOT NULL,
    natural_lang_repr TEXT NOT NULL,
    derivation_method VARCHAR(20) NOT NULL,
    proof_chain TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}',
    confidence FLOAT NOT NULL DEFAULT 0.5,
    source_facts UUID[] NOT NULL,
    validated BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    retracted_at TIMESTAMP WITH TIME ZONE
);

-- Indexes for facts
CREATE INDEX idx_facts_type ON facts(type);
CREATE INDEX idx_facts_layer ON facts(layer);
CREATE INDEX idx_facts_access ON facts(access_count DESC);
CREATE INDEX idx_facts_confidence ON facts(confidence DESC);

-- Indexes for concepts
CREATE INDEX idx_concepts_method ON concepts(derivation_method);
CREATE INDEX idx_concepts_validated ON concepts(validated);
