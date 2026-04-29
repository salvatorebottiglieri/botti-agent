-- 001_initial.sql
-- Core schema: schema_migrations table

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    description TEXT
);

-- Track applied migrations
INSERT INTO schema_migrations (version, description) VALUES (1, 'Initial schema');
