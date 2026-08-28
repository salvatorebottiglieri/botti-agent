"""Tests for per-model pricing table and deterministic cost derivation (issue #87)."""

import json

import pytest

from cortex.config.models import ModelPricing, Settings, derive_cost
from cortex.llm.models import UsageStats


class TestModelPricing:
    """Settings exposes a per-model pricing table with sane defaults."""

    def test_defaults_cover_deepseek_chat(self):
        settings = Settings(llm_api_key="test-key")
        assert "deepseek-chat" in settings.llm_pricing

    def test_defaults_cover_gpt_4o(self):
        settings = Settings(llm_api_key="test-key")
        assert "gpt-4o" in settings.llm_pricing

    def test_default_prices_are_positive(self):
        for pricing in Settings(llm_api_key="test-key").llm_pricing.values():
            assert pricing.input_per_mtok > 0
            assert pricing.output_per_mtok > 0

    def test_custom_pricing_overrides_defaults(self):
        settings = Settings(
            llm_api_key="test-key",
            llm_pricing={
                "my-model": ModelPricing(input_per_mtok=1.5, output_per_mtok=3.0)
            },
        )
        assert settings.llm_pricing == {
            "my-model": ModelPricing(input_per_mtok=1.5, output_per_mtok=3.0),
        }

    def test_pricing_configurable_via_environment(self, monkeypatch):
        monkeypatch.setenv(
            "LLM_PRICING",
            json.dumps({"env-model": {"input_per_mtok": 2.0, "output_per_mtok": 4.0}}),
        )
        settings = Settings(llm_api_key="test-key")
        assert settings.llm_pricing == {
            "env-model": ModelPricing(input_per_mtok=2.0, output_per_mtok=4.0),
        }


class TestDeriveCost:
    """derive_cost is a pure, deterministic function of usage + pricing."""

    PRICING = ModelPricing(input_per_mtok=1.0, output_per_mtok=2.0)

    def test_zero_usage_costs_zero(self):
        assert derive_cost(UsageStats(), self.PRICING) == 0.0

    def test_math_is_prompt_times_input_plus_completion_times_output(self):
        usage = UsageStats(
            prompt_tokens=1_000_000,
            completion_tokens=500_000,
            total_tokens=1_500_000,
        )
        assert derive_cost(usage, self.PRICING) == pytest.approx(2.0)

    def test_deterministic_same_inputs_same_output(self):
        usage = UsageStats(
            prompt_tokens=1234, completion_tokens=567, total_tokens=1801
        )
        assert derive_cost(usage, self.PRICING) == derive_cost(usage, self.PRICING)

    def test_prompt_and_completion_priced_independently(self):
        pricing = ModelPricing(input_per_mtok=0.27, output_per_mtok=1.10)
        usage = UsageStats(prompt_tokens=1000, completion_tokens=1000)
        expected = 0.27 * 1000 / 1_000_000 + 1.10 * 1000 / 1_000_000
        assert derive_cost(usage, pricing) == pytest.approx(expected)

    def test_total_tokens_does_not_influence_cost(self):
        """total_tokens is informational; cost derives from the prompt/completion split."""
        with_extra = UsageStats(
            prompt_tokens=100, completion_tokens=50, total_tokens=999
        )
        normal = UsageStats(
            prompt_tokens=100, completion_tokens=50, total_tokens=150
        )
        assert derive_cost(with_extra, self.PRICING) == derive_cost(
            normal, self.PRICING
        )
