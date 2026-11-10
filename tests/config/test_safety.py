"""Safety-net settings slice (ADR-0021, issue #146).

``SAFETY_STORE_DIR`` is the backup/snapshot/journal root and must live outside
the repo and every Docker volume; retention and the two migration switches are
the knobs the backup service and the migration runner read.

Slices are built with ``_env_file=None`` wherever the assertion is about a
default: a nested ``BaseSettings`` reads ``.env`` itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from cortex.config.loader import load_settings
from cortex.config.models import Settings
from cortex.config.safety import SafetySettings


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No repo ``.env`` and a key for the composed root."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_API_KEY", "safety-test-key")


class TestDefaults:
    def test_defaults_match_the_spec(self) -> None:
        safety = SafetySettings(_env_file=None)

        assert safety.store_dir == Path.home() / ".local" / "share" / "cortex"
        assert safety.backup_retention == 14
        assert safety.backup_on_migration is True
        assert safety.require_backup_before_migration is True

    def test_reachable_from_the_composed_root(self) -> None:
        settings = Settings(
            llm={"api_key": "k"},
            safety={"store_dir": "/tmp/cortex-safety"},
        )

        assert isinstance(settings.safety, SafetySettings)
        assert settings.safety.store_dir == Path("/tmp/cortex-safety")


class TestEnvOverrides:
    def test_every_field_reads_the_safety_namespace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SAFETY_STORE_DIR", "~/cortex-safety")
        monkeypatch.setenv("SAFETY_BACKUP_RETENTION", "3")
        monkeypatch.setenv("SAFETY_BACKUP_ON_MIGRATION", "false")
        monkeypatch.setenv("SAFETY_REQUIRE_BACKUP_BEFORE_MIGRATION", "false")

        safety = SafetySettings(_env_file=None)

        assert safety.store_dir == Path.home() / "cortex-safety"
        assert safety.backup_retention == 3
        assert safety.backup_on_migration is False
        assert safety.require_backup_before_migration is False

    def test_tilde_expands_in_a_constructor_value(self) -> None:
        safety = SafetySettings(store_dir="~/safety", _env_file=None)

        assert safety.store_dir == Path.home() / "safety"

    def test_yaml_section_reaches_the_root(self, tmp_path: Path) -> None:
        """``safety:`` is a loader section, not just a field name."""
        config = tmp_path / "config.yaml"
        config.write_text(
            "safety:\n  store_dir: /tmp/yaml-safety\n  backup_retention: 2\n",
            encoding="utf-8",
        )

        settings = load_settings(config)

        assert settings.safety.store_dir == Path("/tmp/yaml-safety")
        assert settings.safety.backup_retention == 2


class TestValidation:
    def test_zero_retention_is_allowed_and_disables_pruning(self) -> None:
        assert SafetySettings(backup_retention=0, _env_file=None).backup_retention == 0

    def test_negative_retention_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SafetySettings(backup_retention=-1, _env_file=None)

    def test_negative_retention_from_the_environment_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SAFETY_BACKUP_RETENTION", "-1")

        with pytest.raises(ValidationError):
            SafetySettings(_env_file=None)
