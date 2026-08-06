-- 001_initial.sql
-- Core schema: schema_migrations table
-- The application migration runner (cortex.db.migrations.runner) creates
-- this table and records applied versions; initdb must not seed it or the
-- runner's bookkeeping collides (duplicate key / type mismatch).

CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(255) PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT NOW()
);
