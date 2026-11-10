"""`cortex backup:create` / `backup:restore` (ADR-0021, issue #146).

The CLI is a thin shell over ``BackupService``: flags select the URL and store,
restore confirms by default, and no password reaches the output (including the
confirmation prompt).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from cortex.cli import cli
from cortex.config.database import DatabaseSettings
from cortex.config.safety import SafetySettings
from cortex.safety.backup import Backup, BackupError

runner = CliRunner()

DATABASE_URL = "postgresql://settings:pa55@localhost:5433/cortex"


class FakeBackupService:
    instances: list[FakeBackupService] = []
    create_result: Backup | None = None
    restore_result: Backup | None = None
    error: Exception | None = None

    def __init__(
        self,
        safety: SafetySettings,
        database_url: str,
        *,
        store_dir: Path | None = None,
        pg_client: object = None,
    ) -> None:
        self.safety = safety
        self.database_url = database_url
        self.store_dir = store_dir
        self.pg_client = pg_client
        self.resolved: list[str] = []
        self.restored: list[str] = []
        self.restore_force: bool | None = None
        FakeBackupService.instances.append(self)

    def resolve(self, id_or_path: str) -> Path:
        self.resolved.append(id_or_path)
        return Path("/store/backups") / f"{id_or_path}.dump"

    async def create(self) -> Backup:
        if FakeBackupService.error is not None:
            raise FakeBackupService.error
        assert FakeBackupService.create_result is not None
        return FakeBackupService.create_result

    async def restore(self, id_or_path: str, *, force: bool = False) -> Backup:
        self.restored.append(id_or_path)
        self.restore_force = force
        if FakeBackupService.error is not None:
            raise FakeBackupService.error
        assert FakeBackupService.restore_result is not None
        return FakeBackupService.restore_result


@pytest.fixture(autouse=True)
def fake_service(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    FakeBackupService.instances = []
    FakeBackupService.error = None
    created = tmp_path / "cortex-20261110T101500Z.dump"
    created.write_bytes(b"dump")
    FakeBackupService.create_result = Backup(created, 4, datetime.now(UTC))
    FakeBackupService.restore_result = Backup(created, 4, datetime.now(UTC))
    monkeypatch.setattr("cortex.safety.backup.BackupService", FakeBackupService)


@pytest.fixture
def fake_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SimpleNamespace:
    stub = SimpleNamespace(
        safety=SafetySettings(
            store_dir=tmp_path / "store",
            backup_retention=3,
            _env_file=None,
        ),
        database=DatabaseSettings(database_url=DATABASE_URL, _env_file=None),
    )
    monkeypatch.setattr("cortex.config.loader.get_settings", lambda: stub)
    return stub


class TestBackupCreate:
    def test_prints_the_path_and_size(self, fake_settings: SimpleNamespace) -> None:
        result = runner.invoke(cli, ["backup:create"])

        assert result.exit_code == 0, result.output
        service = FakeBackupService.instances[0]
        assert service.safety is fake_settings.safety
        assert service.database_url == DATABASE_URL
        assert str(FakeBackupService.create_result.path) in result.output
        assert "4 bytes" in result.output
        assert "pa55" not in result.output

    def test_out_and_db_url_override_the_store_and_settings(
        self, fake_settings: SimpleNamespace, tmp_path: Path
    ) -> None:
        out = tmp_path / "elsewhere"

        result = runner.invoke(
            cli,
            [
                "backup:create",
                "--out",
                str(out),
                "--db-url",
                "postgresql://flag:flagpass@localhost:5433/cortex",
            ],
        )

        assert result.exit_code == 0, result.output
        service = FakeBackupService.instances[0]
        assert service.store_dir == out
        assert service.database_url == "postgresql://flag:flagpass@localhost:5433/cortex"
        assert "flagpass" not in result.output

    def test_failure_exits_non_zero_without_a_traceback(
        self, fake_settings: SimpleNamespace
    ) -> None:
        FakeBackupService.error = BackupError("pg_dump is missing")

        result = runner.invoke(cli, ["backup:create"])

        assert result.exit_code == 1
        assert "pg_dump is missing" in result.output
        assert "Traceback" not in result.output


class TestBackupRestore:
    def test_confirmation_yes_restores_without_force(
        self, fake_settings: SimpleNamespace
    ) -> None:
        result = runner.invoke(cli, ["backup:restore", "cortex-20261110T101500Z"], input="y\n")

        assert result.exit_code == 0, result.output
        service = FakeBackupService.instances[0]
        assert service.resolved == ["cortex-20261110T101500Z"]
        assert service.restored == ["/store/backups/cortex-20261110T101500Z.dump"]
        assert service.restore_force is False
        assert "restored" in result.output

    def test_confirmation_prompt_shows_the_sanitized_database_url(
        self, fake_settings: SimpleNamespace
    ) -> None:
        result = runner.invoke(cli, ["backup:restore", "some-id"], input="y\n")

        assert result.exit_code == 0, result.output
        assert "postgresql://settings@localhost:5433/cortex" in result.output
        assert "pa55" not in result.output

    def test_declining_the_confirmation_restores_nothing(
        self, fake_settings: SimpleNamespace
    ) -> None:
        result = runner.invoke(cli, ["backup:restore", "some-id"], input="n\n")

        assert result.exit_code != 0
        service = FakeBackupService.instances[0]
        assert service.restored == []

    def test_yes_skips_the_prompt(self, fake_settings: SimpleNamespace) -> None:
        result = runner.invoke(cli, ["backup:restore", "some-id", "--yes"])

        assert result.exit_code == 0, result.output
        assert FakeBackupService.instances[0].restored == [
            "/store/backups/some-id.dump"
        ]

    def test_force_is_forwarded(self, fake_settings: SimpleNamespace) -> None:
        result = runner.invoke(cli, ["backup:restore", "some-id", "--yes", "--force"])

        assert result.exit_code == 0, result.output
        assert FakeBackupService.instances[0].restore_force is True

    def test_db_url_flag_wins_over_settings(
        self, fake_settings: SimpleNamespace
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "backup:restore",
                "some-id",
                "--yes",
                "--db-url",
                "postgresql://flag:flagpass@localhost:5433/cortex",
            ],
        )

        assert result.exit_code == 0, result.output
        assert FakeBackupService.instances[0].database_url == (
            "postgresql://flag:flagpass@localhost:5433/cortex"
        )
        assert "flagpass" not in result.output

    def test_in_use_error_exits_non_zero(
        self, fake_settings: SimpleNamespace
    ) -> None:
        FakeBackupService.error = BackupError(
            "database is in use; stop the app or pass --force"
        )

        result = runner.invoke(cli, ["backup:restore", "some-id", "--yes"])

        assert result.exit_code == 1
        assert "database is in use" in result.output
        assert "Traceback" not in result.output
        assert "pa55" not in result.output
