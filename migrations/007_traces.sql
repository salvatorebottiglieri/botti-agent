-- 007_traces.sql
-- Loop-trace capture: opt-in session flag plus persistent loop-event storage
-- (issue #111 T1). Idempotent: guards make re-runs safe.

-- Opt-in trace flag on sessions. NOT NULL DEFAULT false keeps pre-existing
-- sessions (backfilled false) and flag-less creates untraced.
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS trace_enabled BOOLEAN NOT NULL DEFAULT false;

-- One row per loop event: monotonic seq within a session, wire event_type,
-- and the event's self-describing to_dict() JSON as an opaque payload.
CREATE TABLE IF NOT EXISTS loop_events (
    id BIGSERIAL PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq BIGINT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_loop_events_session ON loop_events(session_id);
CREATE INDEX IF NOT EXISTS idx_loop_events_created ON loop_events(created_at);
