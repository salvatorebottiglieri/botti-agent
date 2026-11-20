-- 011_queued_feedback.sql
-- The feedback queue (issue #169, slice B): a turn waiting for attribution.
-- Mechanism in docs/specs/rule-system.md (### Data model), decision in
-- docs/design/rule-system.md (Ticket #169). Additive only; idempotent guards
-- make re-runs safe.

-- One row per turn waiting for attribution. The rules are the turn's own
-- bundle, copied at enqueue time: attribution may never name a rule the model
-- could not see.
-- `session_id` carries no foreign key on purpose, as in 008: a queued turn is
-- evidence about rules, not session state.
CREATE TABLE IF NOT EXISTS queued_feedback (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL,
    rule_ids UUID[] NOT NULL,
    assistant_turn TEXT NOT NULL,
    user_reply TEXT NOT NULL CHECK (length(btrim(user_reply)) > 0),
    -- pending: waiting for a pass; attributed: a pass classified it, even when
    -- it named no rule; dropped: past the validity window, never attributed.
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    attributed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_queued_feedback_pending
    ON queued_feedback(status, occurred_at);

-- A use remembers the turn it came from: a re-run of a half-written
-- attribution cannot double-score the rules it already wrote.
ALTER TABLE rule_uses ADD COLUMN IF NOT EXISTS feedback_id UUID
    REFERENCES queued_feedback(id) ON DELETE SET NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_rule_uses_feedback_rule
    ON rule_uses(feedback_id, rule_id) WHERE feedback_id IS NOT NULL;
