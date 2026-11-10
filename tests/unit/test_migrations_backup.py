"""Pre-migration snapshot (ADR-0021, issue #146).

``run_migrations`` dumps only when there is pending work, tags the dump
``pre-migration``, and fails closed by default: a failed dump aborts before any
migration runs. The two settings are the documented escape hatches.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cortex.config.database import DatabaseSettings
from cortex.config.safety import SafetySettings
from cortex.db.migrations import runner
from cortex.safety.backup import BackupError

DATABASE_URL = "postgresql://cortex:s3cret@localhost:5433/cortex"

#: Cross-seam ordering log: the fake backup service and connection append here.
EVENTS: list[str] = []


class _AsyncNullContext:
    def __init__(self, value: object = None) -> None:
        self.value = value

    async def __aenter__(self) -> object:
        return self.value

    async def __aexit__(self, *exc: object) -> None:
        return None


class FakeConnection:
    def __init__(self, applied: set[str]) -> None:
        self.applied = applied
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.fetched: list[str] = []

    async def execute(self, query: str, *args: object) -> str:
        EVENTS.append(f"execute:{query.strip()}")
        self.executed.append((query, args))
        return "OK"

    async def fetch(self, query: str) -> list[dict[str, str]]:
        self.fetched.append(query)
        return [{"version": version} for version in self.applied]

    def transaction(self) -> _AsyncNullContext:
        return _AsyncNullContext()


class FakePool:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def acquire(self) -> _AsyncNullContext:
        return _AsyncNullContext(self.connection)


class FakeBackupService:
    instances: list[FakeBackupService] = []
    error: Exception | None = None
    init_error: Exception | None = None

    def __init__(
        self,
        safety: SafetySettings,
        database_url: str,
        *,
        pg_client: object = None,
    ) -> None:
        if FakeBackupService.init_error is not None:
            raise FakeBackupService.init_error
        self.safety = safety
        self.database_url = database_url
        self.pg_client = pg_client
        self.reasons: list[str | None] = []
        FakeBackupService.instances.append(self)

    async def create(self, *, reason: str | None = None) -> SimpleNamespace:
        self.reasons.append(reason)
        EVENTS.append("backup")
        if FakeBackupService.error is not None:
            raise FakeBackupService.error
        return SimpleNamespace(path=Path("/store/backups/cortex-test.dump"))


class FakePgClient:
    instances: list[FakePgClient] = []

    def __init__(self, *, allow_docker_fallback: bool = True) -> None:
        self.allow_docker_fallback = allow_docker_fallback
        FakePgClient.instances.append(self)


def _settings(
    *,
    backup_on_migration: bool = True,
    require_backup_before_migration: bool = True,
) -> SimpleNamespace:
    safety = SafetySettings(
        store_dir=Path("/store"),
        backup_on_migration=backup_on_migration,
        require_backup_before_migration=require_backup_before_migration,
        _env_file=None,
    )
    database = DatabaseSettings(database_url=DATABASE_URL, _env_file=None)
    return SimpleNamespace(safety=safety, database=database)


@pytest.fixture(autouse=True)
def fake_services(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeBackupService.instances = []
    FakeBackupService.error = None
    FakeBackupService.init_error = None
    FakePgClient.instances = []
    EVENTS.clear()
    monkeypatch.setattr(runner, "BackupService", FakeBackupService)
    monkeypatch.setattr(runner, "PgClient", FakePgClient)


@pytest.fixture
def migrations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_initial.sql").write_text("SELECT 1;", encoding="utf-8")
    (migrations_dir / "002_pending.sql").write_text("SELECT 2;", encoding="utf-8")
    monkeypatch.setattr(runner, "MIGRATIONS_DIR", migrations_dir)
    return migrations_dir


async def _run(
    applied: set[str],
    settings: SimpleNamespace | None = None,
    *,
    monkeypatch: pytest.MonkeyPatch,
    connection: FakeConnection | None = None,
) -> FakeConnection:
    connection = connection or FakeConnection(applied)

    async def fake_get_pool() -> FakePool:
        return FakePool(connection)

    monkeypatch.setattr(runner, "get_pool", fake_get_pool)
    await runner.run_migrations(settings or _settings())
    return connection


def _inserted_versions(connection: FakeConnection) -> list[tuple[object, ...]]:
    return [
        args
        for query, args in connection.executed
        if "INSERT INTO schema_migrations" in query
    ]


class TestPendingDetection:
    async def test_no_pending_migrations_takes_no_dump(
        self, migrations: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        connection = await _run({"001_initial", "002_pending"}, monkeypatch=monkeypatch)

        assert FakeBackupService.instances == []
        assert _inserted_versions(connection) == []

    async def test_no_migration_files_takes_no_dump(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.setattr(runner, "MIGRATIONS_DIR", empty)

        await _run(set(), monkeypatch=monkeypatch)

        assert FakeBackupService.instances == []


class TestPendingMigrationDump:
    async def test_pending_migrations_take_a_pre_migration_dump(
        self, migrations: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = _settings()
        connection = await _run({"001_initial"}, settings, monkeypatch=monkeypatch)

        assert len(FakeBackupService.instances) == 1
        service = FakeBackupService.instances[0]
        assert service.safety is settings.safety
        assert service.database_url == DATABASE_URL
        assert service.reasons == ["pre-migration"]
        assert len(FakePgClient.instances) == 1
        assert FakePgClient.instances[0].allow_docker_fallback is False
        assert _inserted_versions(connection) == [("002_pending",)]

    async def test_dump_happens_before_the_first_migration_is_applied(
        self, migrations: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A reorder mutant that applies first and dumps afterwards must fail."""
        await _run({"001_initial"}, monkeypatch=monkeypatch)

        assert "backup" in EVENTS
        assert "execute:SELECT 2;" in EVENTS
        assert EVENTS.index("backup") < EVENTS.index("execute:SELECT 2;")

    async def test_backup_on_migration_false_skips_the_dump(
        self, migrations: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        connection = await _run(
            {"001_initial"},
            _settings(backup_on_migration=False),
            monkeypatch=monkeypatch,
        )

        assert FakeBackupService.instances == []
        assert _inserted_versions(connection) == [("002_pending",)]

    async def test_dump_failure_aborts_before_any_migration(
        self, migrations: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        FakeBackupService.error = BackupError("pg_dump is missing")
        connection = FakeConnection({"001_initial"})

        with pytest.raises(
            RuntimeError,
            match="pre-migration backup failed; refusing to apply migrations",
        ):
            await _run(
                {"001_initial"},
                _settings(require_backup_before_migration=True),
                monkeypatch=monkeypatch,
                connection=connection,
            )

        assert _inserted_versions(connection) == []

    async def test_dump_failure_continues_when_not_required(
        self, migrations: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        FakeBackupService.error = BackupError("pg_dump is missing")

        connection = await _run(
            {"001_initial"},
            _settings(require_backup_before_migration=False),
            monkeypatch=monkeypatch,
        )

        assert _inserted_versions(connection) == [("002_pending",)]

    async def test_constructor_failure_continues_when_not_required(
        self, migrations: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unwritable store fails at construction, not only at dump time."""
        FakeBackupService.init_error = OSError("permission denied")

        connection = await _run(
            {"001_initial"},
            _settings(require_backup_before_migration=False),
            monkeypatch=monkeypatch,
        )

        assert _inserted_versions(connection) == [("002_pending",)]

    async def test_no_dump_when_nothing_is_pending(
        self, migrations: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _run({"001_initial", "002_pending"}, monkeypatch=monkeypatch)

        assert FakeBackupService.instances == []
