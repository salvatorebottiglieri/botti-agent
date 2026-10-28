-- 008_rules.sql
-- Learned rules (issue #130, slice A): the rules table and its use ledger.
-- Mechanism in docs/specs/rule-system.md, decision in docs/adr/0020-learned-rules.md.
-- Idempotent: guards make re-runs safe.

-- One row per rule. `confidence` is always the recomputed posterior over the
-- rule's active rule_uses rows, never an accumulated counter.
CREATE TABLE IF NOT EXISTS rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    condition TEXT NOT NULL,
    action TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    state VARCHAR(20) NOT NULL DEFAULT 'staging',
    confidence FLOAT NOT NULL DEFAULT 0.5,
    origin VARCHAR(20) NOT NULL DEFAULT 'correction',
    superseded_by UUID REFERENCES rules(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    archived_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_rules_state ON rules(state);
CREATE INDEX IF NOT EXISTS idx_rules_confidence ON rules(confidence DESC);

-- One row per application of a rule: the situation it was applied to and the
-- outcome the user's feedback gave it. This table *is* the evidence store for
-- rules — there is no separate rule_evidence table.
--
-- session_id carries no foreign key on purpose: a use is evidence about a
-- rule, not session state, so deleting a session must not silently change a
-- rule's confidence.
CREATE TABLE IF NOT EXISTS rule_uses (
    id BIGSERIAL PRIMARY KEY,
    rule_id UUID NOT NULL REFERENCES rules(id) ON DELETE CASCADE,
    session_id UUID NOT NULL,
    situation TEXT NOT NULL,
    outcome VARCHAR(20) NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rule_uses_rule_occurred ON rule_uses(rule_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_rule_uses_session ON rule_uses(session_id);
