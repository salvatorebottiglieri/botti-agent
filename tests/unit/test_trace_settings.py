"""Settings tests for trace capture configuration (issue #112 T2).

The sidecar base URL (+ per-request timeout) must be configurable: flat
``Settings`` fields with sane defaults, uppercase env overrides
(TRACE_SIDECAR_URL / TRACE_SIDECAR_TIMEOUT_S, flat passthrough like
LLM_PRICING), and an optional nested YAML ``trace`` block mapped to the flat
fields by the loader (mirroring the existing blocks).
"""

from cortex.config.loader import load_settings
from cortex.config.models import Settings


class TestTraceSettingsDefaults:
    def test_sidecar_defaults(self):
        """Flat defaults: localhost sidecar + a sane per-request timeout."""
        settings = Settings(llm_api_key="test-key")
        assert settings.trace_sidecar_url == "http://127.0.0.1:5005"
        assert settings.trace_sidecar_timeout_s == 10.0


class TestTraceSettingsEnvOverrides:
    def test_env_overrides_sidecar_url(self, monkeypatch):
        monkeypatch.setenv("TRACE_SIDECAR_URL", "http://127.0.0.1:6000")
        settings = Settings(llm_api_key="test-key")
        assert settings.trace_sidecar_url == "http://127.0.0.1:6000"

    def test_env_overrides_sidecar_timeout(self, monkeypatch):
        monkeypatch.setenv("TRACE_SIDECAR_TIMEOUT_S", "2.5")
        settings = Settings(llm_api_key="test-key")
        assert settings.trace_sidecar_timeout_s == 2.5

    def test_constructor_kwargs_win_over_env(self, monkeypatch):
        """Explicit constructor values still beat env (init-args precedence)."""
        monkeypatch.setenv("TRACE_SIDECAR_URL", "http://env:1")
        settings = Settings(llm_api_key="test-key", trace_sidecar_url="http://ctor:1")
        assert settings.trace_sidecar_url == "http://ctor:1"


class TestTraceSettingsLoader:
    def test_nested_trace_block_maps_to_flat_fields(self, tmp_path):
        """loader maps a YAML `trace:` block onto the flat Settings fields."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "trace:\n"
            "  sidecar_url: http://rizzo:5005\n"
            "  sidecar_timeout_s: 4.5\n"
        )

        settings = load_settings(config_path=cfg)

        assert settings.trace_sidecar_url == "http://rizzo:5005"
        assert settings.trace_sidecar_timeout_s == 4.5

    def test_loader_without_trace_block_keeps_defaults(self, tmp_path):
        """A config file without a trace block leaves the flat defaults in place."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text("app:\n  port: 8123\n")

        settings = load_settings(config_path=cfg)

        assert settings.app_port == 8123
        assert settings.trace_sidecar_url == "http://127.0.0.1:5005"
        assert settings.trace_sidecar_timeout_s == 10.0

    def test_env_override_reaches_settings_when_no_yaml_trace_block(self, tmp_path, monkeypatch):
        """Flat env passthrough (LLM_PRICING-style): with no YAML value present
        the env var supplies the field."""
        monkeypatch.setenv("TRACE_SIDECAR_URL", "http://127.0.0.1:7000")
        cfg = tmp_path / "config.yaml"
        cfg.write_text("app:\n  port: 8123\n")

        settings = load_settings(config_path=cfg)

        assert settings.trace_sidecar_url == "http://127.0.0.1:7000"
