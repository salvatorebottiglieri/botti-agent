"""``load_settings``: YAML section → composed model (issue #35).

L2 — precedence ``YAML > env > default`` is preserved.

This is the *measured* law: ``load_settings`` passes the YAML section as a
constructor argument, and pydantic-settings ranks an explicit argument above an
environment variable. The docstring claimed the opposite order; that claim was
stale and has been corrected, and the precedence defect itself is filed as #125
(out of scope here).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from cortex.config.app import AppSettings
from cortex.config.database import DatabaseSettings
from cortex.config.llm import LLMSettings
from cortex.config.loader import load_settings
from cortex.config.logging import LoggingSettings
from cortex.config.mqtt import MQTTSettings
from cortex.config.trace import TraceSettings


@pytest.fixture(autouse=True)
def _hermetic_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Run every loader test from an empty directory so neither the developer's
    ``.env`` nor the repository ``config.yaml`` can leak into an assertion.

    Tests pass ``config_path`` explicitly.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_API_KEY", "loader-test-key")


def _write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    return path


class TestPrecedence:
    """L2 — negation: the env value stops losing to the YAML section, or a
    default beats the YAML value."""

    def test_yaml_beats_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = _write_config(tmp_path, "llm:\n  model: yaml-model\napp:\n  port: 8123\n")
        monkeypatch.setenv("LLM_MODEL", "env-model")
        monkeypatch.setenv("APP_PORT", "9999")

        settings = load_settings(config)

        assert settings.llm.model == "yaml-model"
        assert settings.app.port == 8123

    def test_yaml_beats_default(self, tmp_path: Path) -> None:
        config = _write_config(tmp_path, "database:\n  pool_min_size: 9\n")

        settings = load_settings(config)

        assert settings.database.db_pool_min_size == 9
        assert settings.database.db_pool_min_size != DatabaseSettings.model_fields[
            "db_pool_min_size"
        ].default

    def test_env_beats_default_when_yaml_is_silent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = _write_config(tmp_path, "database:\n  url: postgresql://yaml/db\n")
        monkeypatch.setenv("APP_PORT", "9999")

        settings = load_settings(config)

        assert settings.app.port == 9999

    def test_unresolved_env_ref_falls_back_to_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``${VAR}`` whose variable is unset is dropped, so the environment
        variable of the same name (or the default) applies."""
        config = _write_config(tmp_path, "llm:\n  base_url: ${CORTEX_TEST_UNSET_REF}\n")
        monkeypatch.setenv("LLM_BASE_URL", "https://env.example")

        settings = load_settings(config)

        assert settings.llm.base_url == "https://env.example"

    def test_unresolved_env_ref_falls_back_to_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = _write_config(tmp_path, "llm:\n  base_url: ${CORTEX_TEST_UNSET_REF}\n")
        monkeypatch.delenv("LLM_BASE_URL", raising=False)

        settings = load_settings(config)

        assert settings.llm.base_url == LLMSettings.model_fields["base_url"].default

    def test_resolved_env_ref_is_used(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = _write_config(tmp_path, "llm:\n  api_key: ${CORTEX_TEST_KEY}\n")
        monkeypatch.setenv("CORTEX_TEST_KEY", "resolved-key")

        settings = load_settings(config)

        assert settings.llm.api_key.get_secret_value() == "resolved-key"


class TestSectionMapping:
    """Every YAML section lands in its slice, with the YAML key names of
    ``config.yaml`` unchanged (a user-facing contract)."""

    def test_all_sections_map_to_their_slice(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = _write_config(
            tmp_path,
            """
database:
  url: postgresql://yaml/db
  pool_min_size: 2
  pool_max_size: 8
  pool_timeout: 12
llm:
  provider: openai
  api_key: yaml-key
  model: yaml-model
  base_url: https://yaml.example
  timeout: 21
mqtt:
  broker_url: mqtt://yaml:1883
  username: yaml-user
  keepalive: 33
  reconnect_interval: 4
app:
  host: 10.0.0.1
  port: 7100
  reload: true
  workers: 2
trace:
  sidecar_url: http://yaml:5005
  sidecar_timeout_s: 3.5
  retention_days: 5
logging:
  level: ERROR
  format: json
  include_trace_id: false
""",
        )

        settings = load_settings(config)

        assert settings.database.database_url == "postgresql://yaml/db"
        assert settings.database.db_pool_min_size == 2
        assert settings.database.db_pool_max_size == 8
        assert settings.database.db_pool_timeout == 12

        assert settings.llm.provider == "openai"
        assert settings.llm.api_key.get_secret_value() == "yaml-key"
        assert settings.llm.model == "yaml-model"
        assert settings.llm.base_url == "https://yaml.example"
        assert settings.llm.timeout == 21

        assert settings.mqtt.broker_url == "mqtt://yaml:1883"
        assert settings.mqtt.username == "yaml-user"
        assert settings.mqtt.keepalive == 33
        assert settings.mqtt.reconnect_interval == 4

        assert settings.app.host == "10.0.0.1"
        assert settings.app.port == 7100
        assert settings.app.reload is True
        assert settings.app.workers == 2

        assert settings.trace.sidecar_url == "http://yaml:5005"
        assert settings.trace.sidecar_timeout_s == 3.5
        assert settings.trace.retention_days == 5

        assert settings.logging.level == "ERROR"
        assert settings.logging.format == "json"
        assert settings.logging.include_trace_id is False

    def test_unsupported_section_key_is_ignored(self, tmp_path: Path) -> None:
        config = _write_config(tmp_path, "database:\n  url: postgresql://yaml/db\n  nope: 1\n")

        settings = load_settings(config)

        assert settings.database.database_url == "postgresql://yaml/db"

    def test_defaults_apply_without_a_config_file(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The Docker path: no ``config.yaml`` in the image, so the environment
        is the only source."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://docker/db")
        monkeypatch.setenv("MQTT_BROKER_URL", "mqtt://docker:1883")

        settings = load_settings()

        assert settings.database.database_url == "postgresql://docker/db"
        assert settings.mqtt.broker_url == "mqtt://docker:1883"
        assert settings.logging.level == LoggingSettings.model_fields["level"].default
        assert settings.app.port == AppSettings.model_fields["port"].default
        assert settings.trace.retention_days == TraceSettings.model_fields["retention_days"].default
        assert settings.mqtt.client_id_prefix == MQTTSettings.model_fields[
            "client_id_prefix"
        ].default

    def test_missing_api_key_still_fails_loudly(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A config file without the secret does not make the secret optional."""
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        config = _write_config(tmp_path, "llm:\n  model: yaml-model\n")

        with pytest.raises(ValidationError, match="api_key"):
            load_settings(config)
