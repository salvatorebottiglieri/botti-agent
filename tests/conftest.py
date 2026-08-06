"""Pytest configuration for asyncio tests."""

import os

import pytest

# Real Settings require llm_api_key; config.yaml's ${OPENAI_API_KEY}
# placeholder is dropped when the variable is unset, so give tests a dummy key.
os.environ.setdefault("LLM_API_KEY", "test-key")

# pytest-asyncio handles event loop setup automatically
# No custom event_loop_policy fixture needed
