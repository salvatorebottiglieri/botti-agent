"""Settings tests for trace capture configuration (issue #112 T2).

The sidecar base URL (+ per-request timeout) and the retention window must be
configurable: fields on the ``trace`` slice with sane defaults, uppercase env
overrides (TRACE_SIDECAR_URL / TRACE_SIDECAR_TIMEOUT_S / TRACE_RETENTION_DAYS),
and an optional nested YAML ``trace`` block mapped onto the slice by the loader.

Since #35 the trace fields live on :class:`cortex.config.trace.TraceSettings`
(``settings.trace``) instead of on the flat root model.
"""

from cortex.config.loader import load_settings
from cortex.config.trace import TraceSettings


class TestTraceSettingsDefaults:
    def test_sidecar_defaults(self):
        """Slice defaults: localhost sidecar + a sane per-request timeout."""
        trace = TraceSettings(_env_file=None)
        assert trace.sidecar_url == "http://127.0.0.1:5005"
        assert trace.sidecar_timeout_s == 10.0


class TestTraceSettingsEnvOverrides:
    def test_env_overrides_sidecar_url(self, monkeypatch):
        monkeypatch.setenv("TRACE_SIDECAR_URL", "http://127.0.0.1:6000")
        trace = TraceSettings(_env_file=None)
        assert trace.sidecar_url == "http://127.0.0.1:6000"

    def test_env_overrides_sidecar_timeout(self, monkeypatch):
        monkeypatch.setenv("TRACE_SIDECAR_TIMEOUT_S", "2.5")
        trace = TraceSettings(_env_file=None)
        assert trace.sidecar_timeout_s == 2.5

    def test_env_beats_constructor_kwargs(self, monkeypatch):
        """An exported variable outranks the constructor argument (ADR-0019)."""
        monkeypatch.setenv("TRACE_SIDECAR_URL", "http://env:1")
        trace = TraceSettings(sidecar_url="http://ctor:1", _env_file=None)
        assert trace.sidecar_url == "http://env:1"


class TestTraceSettingsLoader:
    def test_nested_trace_block_maps_to_slice_fields(self, tmp_path):
        """loader maps a YAML `trace:` block onto the trace slice fields."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "trace:\n"
            "  sidecar_url: http://rizzo:5005\n"
            "  sidecar_timeout_s: 4.5\n"
        )

        settings = load_settings(config_path=cfg)

        assert settings.trace.sidecar_url == "http://rizzo:5005"
        assert settings.trace.sidecar_timeout_s == 4.5

    def test_loader_without_trace_block_keeps_defaults(self, tmp_path):
        """A config file without a trace block leaves the defaults in place."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text("app:\n  port: 8123\n")

        settings = load_settings(config_path=cfg)

        assert settings.app.port == 8123
        assert settings.trace.sidecar_url == "http://127.0.0.1:5005"
        assert settings.trace.sidecar_timeout_s == 10.0

    def test_env_override_reaches_settings_when_no_yaml_trace_block(self, tmp_path, monkeypatch):
        """Env passthrough: with no YAML value present the env var supplies the
        field."""
        monkeypatch.setenv("TRACE_SIDECAR_URL", "http://127.0.0.1:7000")
        cfg = tmp_path / "config.yaml"
        cfg.write_text("app:\n  port: 8123\n")

        settings = load_settings(config_path=cfg)

        assert settings.trace.sidecar_url == "http://127.0.0.1:7000"


class TestTraceRetentionDefaults:
    """Default for the loop-event retention window (issue #114 T4)."""

    def test_retention_defaults_to_thirty_days(self):
        """A sane default keeps a month of traces before cleanup is needed."""
        assert TraceSettings(_env_file=None).retention_days == 30


class TestTraceRetentionEnvOverrides:
    def test_env_overrides_retention_days(self, monkeypatch):
        monkeypatch.setenv("TRACE_RETENTION_DAYS", "7")
        assert TraceSettings(_env_file=None).retention_days == 7

    def test_env_beats_constructor_kwargs(self, monkeypatch):
        """An exported variable outranks the constructor argument (ADR-0019)."""
        monkeypatch.setenv("TRACE_RETENTION_DAYS", "7")
        trace = TraceSettings(retention_days=14, _env_file=None)
        assert trace.retention_days == 7


class TestTraceRetentionLoader:
    def test_nested_trace_block_maps_retention_days(self, tmp_path):
        """loader maps YAML trace.retention_days onto the slice field."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text("trace:\n  retention_days: 14\n")

        settings = load_settings(config_path=cfg)

        assert settings.trace.retention_days == 14

    def test_loader_without_retention_key_keeps_default(self, tmp_path):
        """A trace block without retention_days leaves the default in place."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text("trace:\n  sidecar_url: http://rizzo:5005\n")

        settings = load_settings(config_path=cfg)

        assert settings.trace.retention_days == 30
        assert settings.trace.sidecar_url == "http://rizzo:5005"

    def test_env_override_reaches_settings_when_no_yaml_retention(self, tmp_path, monkeypatch):
        """Env passthrough: with no YAML retention_days present the env var
        supplies the field — and would outrank one anyway (ADR-0019)."""
        monkeypatch.setenv("TRACE_RETENTION_DAYS", "60")
        cfg = tmp_path / "config.yaml"
        cfg.write_text("trace:\n  sidecar_url: http://rizzo:5005\n")

        settings = load_settings(config_path=cfg)

        assert settings.trace.retention_days == 60
