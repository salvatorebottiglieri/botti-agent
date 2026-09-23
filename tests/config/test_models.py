"""Composed settings slices (issue #35 / ADR-0010).

Invariants under test — from the design annotation on #35:

* **L1** Every legacy env-var name still configures its field.
* **L2** Precedence ``env > YAML > .env > default`` (ADR-0019, fixing #125).
* **L3** A slice reads only its own section.
* **L4** A missing required secret fails loudly.

Slices are constructed with ``_env_file=None`` wherever the assertion is about
a *default*: a nested ``BaseSettings`` reads ``.env`` itself, and a root-level
``_env_file=None`` does not propagate to it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_settings import BaseSettings

from cortex.config.app import AppSettings
from cortex.config.database import DatabaseSettings
from cortex.config.learning import LearningSettings
from cortex.config.llm import LLMSettings, ModelPricing, derive_cost
from cortex.config.logging import LoggingSettings
from cortex.config.models import Settings
from cortex.config.mqtt import MQTTSettings
from cortex.config.trace import TraceSettings
from tests.settings_env import SETTINGS_ENV_NAMES


class TestFrozenEnvNames:
    """L1 — every legacy env-var name still configures its field.

    Negation: setting one of these names no longer reaches the composed field.
    """

    def test_database_names(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://frozen/db")
        monkeypatch.setenv("DB_POOL_MIN_SIZE", "3")
        monkeypatch.setenv("DB_POOL_MAX_SIZE", "7")
        monkeypatch.setenv("DB_POOL_TIMEOUT", "11")

        db = DatabaseSettings(_env_file=None)

        assert db.database_url == "postgresql://frozen/db"
        assert db.db_pool_min_size == 3
        assert db.db_pool_max_size == 7
        assert db.db_pool_timeout == 11

    def test_mqtt_names(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MQTT_BROKER_URL", "mqtt://frozen:1883")
        monkeypatch.setenv("MQTT_USERNAME", "frozen-user")
        monkeypatch.setenv("MQTT_PASSWORD", "frozen-pass")
        monkeypatch.setenv("MQTT_CLIENT_ID_PREFIX", "frozen-prefix")
        monkeypatch.setenv("MQTT_KEEPALIVE", "42")
        monkeypatch.setenv("MQTT_RECONNECT_INTERVAL", "9")

        mqtt = MQTTSettings(_env_file=None)

        assert mqtt.broker_url == "mqtt://frozen:1883"
        assert mqtt.username == "frozen-user"
        assert mqtt.password is not None
        assert mqtt.password.get_secret_value() == "frozen-pass"
        assert mqtt.client_id_prefix == "frozen-prefix"
        assert mqtt.keepalive == 42
        assert mqtt.reconnect_interval == 9

    def test_llm_names(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("LLM_API_KEY", "frozen-key")
        monkeypatch.setenv("LLM_MODEL", "frozen-model")
        monkeypatch.setenv("LLM_BASE_URL", "https://frozen.example")
        monkeypatch.setenv("LLM_TIMEOUT", "17")

        llm = LLMSettings(_env_file=None)

        assert llm.provider == "anthropic"
        assert llm.api_key.get_secret_value() == "frozen-key"
        assert llm.model == "frozen-model"
        assert llm.base_url == "https://frozen.example"
        assert llm.timeout == 17

    def test_llm_judge_model_and_pricing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_API_KEY", "frozen-key")
        monkeypatch.setenv("LLM_JUDGE_MODEL", "frozen-judge")
        monkeypatch.setenv(
            "LLM_PRICING",
            '{"frozen-model": {"input_per_mtok": 1.5, "output_per_mtok": 2.5}}',
        )

        llm = LLMSettings(_env_file=None)

        assert llm.judge_model == "frozen-judge"
        assert llm.pricing["frozen-model"] == ModelPricing(
            input_per_mtok=1.5, output_per_mtok=2.5
        )

    def test_app_names(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_HOST", "127.0.0.1")
        monkeypatch.setenv("APP_PORT", "8123")
        monkeypatch.setenv("APP_RELOAD", "true")
        monkeypatch.setenv("APP_WORKERS", "4")

        app = AppSettings(_env_file=None)

        assert app.host == "127.0.0.1"
        assert app.port == 8123
        assert app.reload is True
        assert app.workers == 4

    def test_logging_names(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("LOG_FORMAT", "json")
        monkeypatch.setenv("LOG_INCLUDE_TRACE_ID", "false")

        logging_settings = LoggingSettings(_env_file=None)

        assert logging_settings.level == "DEBUG"
        assert logging_settings.format == "json"
        assert logging_settings.include_trace_id is False

    def test_trace_names(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRACE_SIDECAR_URL", "http://frozen:5005")
        monkeypatch.setenv("TRACE_SIDECAR_TIMEOUT_S", "2.5")
        monkeypatch.setenv("TRACE_RETENTION_DAYS", "14")

        trace = TraceSettings(_env_file=None)

        assert trace.sidecar_url == "http://frozen:5005"
        assert trace.sidecar_timeout_s == 2.5
        assert trace.retention_days == 14

    @pytest.mark.parametrize(
        ("field", "legacy_name", "prefixed_name", "value", "expected"),
        [
            ("circuit_breaker_threshold", "CIRCUIT_BREAKER_THRESHOLD",
             "LLM_CIRCUIT_BREAKER_THRESHOLD", "7", 7),
            ("circuit_breaker_timeout", "CIRCUIT_BREAKER_TIMEOUT",
             "LLM_CIRCUIT_BREAKER_TIMEOUT", "12.5", 12.5),
            ("circuit_breaker_half_open_successes", "CIRCUIT_BREAKER_HALF_OPEN_SUCCESSES",
             "LLM_CIRCUIT_BREAKER_HALF_OPEN_SUCCESSES", "2", 2),
        ],
    )
    def test_circuit_breaker_accepts_legacy_and_prefixed_names(
        self,
        monkeypatch: pytest.MonkeyPatch,
        field: str,
        legacy_name: str,
        prefixed_name: str,
        value: str,
        expected: int | float,
    ) -> None:
        """The three circuit-breaker fields kept their unprefixed env names when
        they moved under the ``LLM_`` prefix, and gained the prefixed spelling.
        Both must resolve; dropping the legacy name would silently ignore an
        operator's existing environment."""
        monkeypatch.setenv("LLM_API_KEY", "frozen-key")

        monkeypatch.setenv(legacy_name, value)
        assert getattr(LLMSettings(_env_file=None), field) == expected
        monkeypatch.delenv(legacy_name)

        monkeypatch.setenv(prefixed_name, value)
        assert getattr(LLMSettings(_env_file=None), field) == expected


class TestSliceIsolation:
    """L3 — a slice reads only its own section.

    Negation: ``DATABASE_URL`` changes ``settings.llm`` or ``settings.mqtt``
    (or another slice's env var leaks across).
    """

    def test_database_env_does_not_leak_into_other_slices(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://only-db/db")

        llm = LLMSettings(api_key="k", _env_file=None)
        mqtt = MQTTSettings(_env_file=None)
        app = AppSettings(_env_file=None)

        assert llm.model == LLMSettings.model_fields["model"].default
        assert mqtt.broker_url == MQTTSettings.model_fields["broker_url"].default
        assert app.host == AppSettings.model_fields["host"].default

    def test_llm_env_does_not_leak_into_other_slices(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLM_MODEL", "only-llm")

        db = DatabaseSettings(_env_file=None)
        trace = TraceSettings(_env_file=None)

        assert db.database_url == DatabaseSettings.model_fields["database_url"].default
        assert trace.retention_days == TraceSettings.model_fields["retention_days"].default

    def test_root_slices_stay_independent(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        # chdir so the nested BaseSettings cannot pick up a developer's .env.
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DATABASE_URL", "postgresql://only-db/db")
        monkeypatch.setenv("LLM_API_KEY", "k")

        settings = Settings()

        assert settings.database.database_url == "postgresql://only-db/db"
        assert settings.app.port == AppSettings.model_fields["port"].default
        assert settings.mqtt.broker_url == MQTTSettings.model_fields["broker_url"].default


class TestMissingSecretFailsLoudly:
    """L4 — a missing required secret fails loudly.

    Negation: ``Settings()`` constructs successfully with no ``LLM_API_KEY``
    anywhere. A silently-dropped flat kwarg (``Settings(llm_api_key=...)``) must
    therefore surface as "Field required", never as a half-configured object.
    """

    def test_llm_slice_requires_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LLM_API_KEY", raising=False)

        with pytest.raises(ValidationError, match="api_key"):
            LLMSettings(_env_file=None)

    def test_root_requires_api_key(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("LLM_API_KEY", raising=False)

        with pytest.raises(ValidationError, match="api_key"):
            Settings()

    def test_flat_root_kwarg_is_not_silently_accepted_as_configuration(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """The migration hazard: flat kwargs no longer exist. With no
        ``LLM_API_KEY`` in the environment this must raise rather than build a
        Settings whose llm slice ignored the kwarg."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("LLM_API_KEY", raising=False)

        with pytest.raises(ValidationError):
            Settings(llm_api_key="test-key")  # type: ignore[call-arg]


class TestComposedRoot:
    """Acceptance: the root holds one instance of each slice, loads from env,
    from nested YAML-shaped input, and validates at the composed level."""

    def test_root_holds_every_slice(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("LLM_API_KEY", "k")

        settings = Settings()

        assert isinstance(settings.database, DatabaseSettings)
        assert isinstance(settings.llm, LLMSettings)
        assert isinstance(settings.mqtt, MQTTSettings)
        assert isinstance(settings.app, AppSettings)
        assert isinstance(settings.logging, LoggingSettings)
        assert isinstance(settings.trace, TraceSettings)
        assert isinstance(settings.learning, LearningSettings)

    def test_root_loads_from_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("LLM_API_KEY", "env-key")
        monkeypatch.setenv("LLM_MODEL", "env-model")
        monkeypatch.setenv("DATABASE_URL", "postgresql://env/db")
        monkeypatch.setenv("APP_PORT", "9999")
        monkeypatch.setenv("LOG_LEVEL", "WARNING")

        settings = Settings()

        assert settings.llm.api_key.get_secret_value() == "env-key"
        assert settings.llm.model == "env-model"
        assert settings.database.database_url == "postgresql://env/db"
        assert settings.app.port == 9999
        assert settings.logging.level == "WARNING"

    def test_root_accepts_nested_sections(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        monkeypatch.chdir(tmp_path)

        settings = Settings(
            version="9.9.9",
            database={"database_url": "postgresql://nested/db", "db_pool_min_size": 2},
            llm={"api_key": "nested-key", "model": "nested-model"},
            mqtt={"broker_url": "mqtt://nested:1883"},
            app={"port": 7000},
            logging={"format": "json"},
            trace={"retention_days": 3},
            learning={"reservoir_size": 42},
        )

        assert settings.version == "9.9.9"
        assert settings.database.database_url == "postgresql://nested/db"
        assert settings.database.db_pool_min_size == 2
        assert settings.llm.api_key.get_secret_value() == "nested-key"
        assert settings.llm.model == "nested-model"
        assert settings.mqtt.broker_url == "mqtt://nested:1883"
        assert settings.app.port == 7000
        assert settings.logging.format == "json"
        assert settings.trace.retention_days == 3
        assert settings.learning.reservoir_size == 42

    def test_validation_happens_at_the_slice_level(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.chdir(tmp_path)

        with pytest.raises(ValidationError, match="port"):
            Settings(llm={"api_key": "k"}, app={"port": 0})


class TestSourcePrecedence:
    """L2 — the environment outranks a constructor argument, at the root and
    inside a nested slice (CFG3).

    Negation: the nested kwarg (``app={"port": 7000}``) is returned while
    ``APP_PORT`` is exported.
    """

    def test_env_beats_nested_constructor_kwarg(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("APP_PORT", "9999")

        settings = Settings(llm={"api_key": "k"}, app={"port": 7000})

        assert settings.app.port == 9999

    def test_exported_variable_does_not_reach_a_test(self) -> None:
        """CFG4 — the autouse scrub keeps an exported settings variable out of
        the suite. The child below exports ``LLM_API_KEY``/``LLM_MODEL`` and
        ``APP_PORT`` (which outrank the kwargs under test) plus a bare ``APP``
        (which the scrub's name set has to catch too, or the app slice eats it
        as the whole field value)."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/config/test_models.py::TestComposedRoot::test_root_accepts_nested_sections",
            ],
            cwd=Path(__file__).resolve().parents[2],
            env={
                **os.environ,
                "LLM_API_KEY": "ambient",
                "LLM_MODEL": "ambient",
                "APP_PORT": "9999",
                "APP": "1",
            },
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stdout + result.stderr


class TestAmbientEnvGuard:
    """CFG4 — the autouse scrub covers every namespace the slices declare.

    Negation A: a slice declaring a new ``env_prefix`` leaks its ambient
    variable into every test until someone remembers to add it to
    ``tests/settings_env.py::SETTINGS_ENV_PREFIXES``.
    Negation B: a root field added to ``Settings`` and not added to
    ``tests/settings_env.py::SETTINGS_ENV_NAMES`` leaks its bare variable
    (``APP``, ``VERSION``, …) the same way.
    """

    def test_every_settings_env_name_is_scrubbed(self) -> None:
        slice_prefixes = {
            field.annotation.model_config.get("env_prefix")
            for field in Settings.model_fields.values()
            if isinstance(field.annotation, type) and issubclass(field.annotation, BaseSettings)
        } - {None, ""}
        root_field_names = {name.upper() for name in Settings.model_fields}

        assert slice_prefixes <= SETTINGS_ENV_NAMES
        assert root_field_names <= SETTINGS_ENV_NAMES


class TestLearningSettingsStub:
    """#35 ships the stub only; wiring the ESN onto it is #12."""

    def test_defaults_match_adr_0001(self) -> None:
        learning = LearningSettings(_env_file=None)

        assert learning.reservoir_size == 500
        assert learning.spectral_radius == 0.9
        assert learning.leak_rate == 0.3

    def test_reachable_from_root(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("LLM_API_KEY", "k")

        assert isinstance(Settings().learning, LearningSettings)

    def test_env_namespace_is_prefixed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The slice reads ``LEARNING_*``, never the bare generic field names.

        Negation: with no ``env_prefix`` on the slice, a process-wide
        ``RESERVOIR_SIZE`` / ``SPECTRAL_RADIUS`` / ``LEAK_RATE`` would
        configure the ESN — the same generic-name hazard the circuit-breaker
        aliases were restricted to avoid.
        """
        monkeypatch.setenv("LEARNING_RESERVOIR_SIZE", "128")
        monkeypatch.setenv("RESERVOIR_SIZE", "999")

        learning = LearningSettings(_env_file=None)

        assert learning.reservoir_size == 128
        assert learning.reservoir_size != 999


class TestImportGraph:
    """The composed root is imported first by many entry points, so importing
    it must not depend on the LLM package's import order."""

    def test_legacy_llm_config_path_still_re_exports(self) -> None:
        """F-A: ``cortex.llm.config`` keeps its pre-move import surface."""
        from cortex import llm

        legacy = llm.config

        assert legacy.LLMSettings is LLMSettings
        assert legacy.ModelPricing is ModelPricing
        assert legacy.derive_cost is derive_cost
        assert hasattr(legacy, "GenerationConfig")

    @pytest.mark.parametrize(
        "target",
        ["cortex.config.models", "cortex.llm.config", "cortex", "cortex.config.loader"],
    )
    def test_importable_in_a_fresh_interpreter(self, target: str) -> None:
        subprocess.run(
            [sys.executable, "-c", f"import {target}"],
            check=True,
            capture_output=True,
        )

    def test_config_layer_does_not_import_the_llm_package(self) -> None:
        """The config layer must not depend on the ``llm`` package (F-A).

        Negation, first assertion: a reintroduced ``config -> llm`` edge —
        ``LLMSettings`` imported from ``cortex.llm.config`` instead of
        ``cortex.config.llm`` — puts ``cortex.llm`` in ``sys.modules``. This is
        the edge itself, so it still fails if ``cortex.llm.__init__`` ever stops
        importing eagerly.

        Negation, second assertion: that edge executes ``cortex.llm.__init__``
        today, which imports the factory and the OpenAI provider, so
        ``import openai`` happens.

        Runs in a subprocess: other tests import both, so an in-process
        ``sys.modules`` check could not fail.
        """
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import cortex.config.models, sys; "
                "assert 'cortex.llm' not in sys.modules, "
                "'cortex.config.models must not import the cortex.llm package'; "
                "assert 'openai' not in sys.modules, "
                "'cortex.config.models must not load the openai SDK'",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr
