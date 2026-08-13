-- 006_message_tool_call_id.sql
-- Persist tool_call_id on messages so persisted goal/chat conversations
-- keep the assistant-tool_call -> tool_result link required by LLM providers.

ALTER TABLE messages ADD COLUMN IF NOT EXISTS tool_call_id VARCHAR(255);
