-- Migration: 005_api_and_minions.sql
-- Description: API keys for authentication and minions registry

-- API keys table (for authentication)
CREATE TABLE IF NOT EXISTS api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash VARCHAR(255) UNIQUE NOT NULL,
    name VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    revoked_at TIMESTAMP WITH TIME ZONE,
    last_used_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_api_keys_key_hash ON api_keys(key_hash) WHERE revoked_at IS NULL;

-- Minions registry table
CREATE TABLE IF NOT EXISTS minions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    minion_id VARCHAR(255) UNIQUE NOT NULL,
    minion_type VARCHAR(50) NOT NULL,
    minion_version VARCHAR(50),
    registered_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_heartbeat_at TIMESTAMP WITH TIME ZONE,
    last_known_ip VARCHAR(45),
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_minions_minion_id ON minions(minion_id);
CREATE INDEX IF NOT EXISTS idx_minions_type ON minions(minion_type);
CREATE INDEX IF NOT EXISTS idx_minions_heartbeat ON minions(last_heartbeat_at);

-- Goals table for execution module
CREATE TABLE IF NOT EXISTS goals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    description TEXT NOT NULL,
    priority VARCHAR(20) NOT NULL DEFAULT 'normal',
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    ended_at TIMESTAMP WITH TIME ZONE,
    error TEXT,
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status);
CREATE INDEX IF NOT EXISTS idx_goals_created_at ON goals(created_at DESC);

-- Goal steps table
CREATE TABLE IF NOT EXISTS goal_steps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    goal_id UUID NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    step_number INTEGER NOT NULL,
    action TEXT NOT NULL,
    result TEXT,
    error TEXT,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_goal_steps_goal_id ON goal_steps(goal_id);