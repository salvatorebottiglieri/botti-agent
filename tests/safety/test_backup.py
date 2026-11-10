"""Database backup/restore service (ADR-0021, issue #146).

The contract under test:

* a created backup is a custom-format dump, mode ``0600``, atomically renamed
  from a temp file in the same directory, and pruned by retention;
* the connection password travels in ``PGPASSWORD``, never on a command line or
  in output;
* ``pg_dump`` is preferred on ``PATH``; the local compose container is the
  fallback, and anything else fails loudly;
* restore refuses while the target database has other connections unless
  forced, and the check connection is always closed.
"""

from __future__ import annotations

import logging
import re
import stat
from datetime import UTC
from pathlib import Path

import pytest

from cortex.config.safety import SafetySettings
from cortex.safety.backup import (
    Backup,
    BackupError,
    BackupService,
    PgClient,
    sanitize_database_url,
)

DATABASE_URL = "postgresql://cortex:s3cret@localhost:5433/cortex"
BACKUP_NAME = re.compile(r"^cortex-\d{8}T\d{6}Z(-\d+)?\.dump$")


class FakeProcess:
    def __init__(self, *, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.stdin_data: bytes | None = None

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        self.stdin_data = input
        return self.stdout, self.stderr


class RecordingRunner:
    """Records each argv/env pair and simulates pg_dump's ``--file`` output."""

    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
        write_file: bool = True,
    ) -> None:
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.write_file = write_file
        self.process: FakeProcess | None = None

    async def __call__(self, *argv: str, **kwargs: object) -> FakeProcess:
        self.calls.append((argv, kwargs))
        self.process = FakeProcess(
            returncode=self.returncode, stdout=self.stdout, stderr=self.stderr
        )
        if self.write_file and "--file" in argv:
            Path(argv[argv.index("--file") + 1]).write_bytes(self.stdout or b"dump")
        return self.process


class FakePgClient:
    """The backup service's seam to pg_dump/pg_restore."""

    def __init__(self, *, payload: bytes = b"x" * 32, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.dump_calls: list[tuple[str, Path]] = []
        self.restore_calls: list[tuple[str, Path]] = []

    async def dump(self, database_url: str, target: Path) -> None:
        self.dump_calls.append((database_url, target))
        if self.error is not None:
            raise self.error
        target.write_bytes(self.payload)

    async def restore(self, database_url: str, source: Path) -> None:
        self.restore_calls.append((database_url, source))
        if self.error is not None:
            raise self.error


class FakeConnection:
    def __init__(self, other_connections: int):
        self.other_connections = other_connections
        self.query: str | None = None
        self.closed = False

    async def fetchval(self, query: str) -> int:
        self.query = query
        return self.other_connections

    async def close(self) -> None:
        self.closed = True


class FakeConnectionFactory:
    def __init__(self, other_connections: int = 0):
        self.other_connections = other_connections
        self.connection = FakeConnection(other_connections)
        self.calls: list[str] = []

    async def __call__(self, database_url: str) -> FakeConnection:
        self.calls.append(database_url)
        return self.connection


def _settings(tmp_path: Path, *, retention: int = 14) -> SafetySettings:
    return SafetySettings(
        store_dir=tmp_path / "store",
        backup_retention=retention,
        _env_file=None,
    )


def _service(
    tmp_path: Path,
    *,
    retention: int = 14,
    pg_client: FakePgClient | None = None,
    connection_factory: FakeConnectionFactory | None = None,
) -> BackupService:
    return BackupService(
        _settings(tmp_path, retention=retention),
        DATABASE_URL,
        pg_client=pg_client or FakePgClient(),
        connection_factory=connection_factory or FakeConnectionFactory(),
    )


def _which(*present: str):
    return lambda name: f"/usr/bin/{name}" if name in present else None


def _host_client(
    runner: RecordingRunner, *, tools: tuple[str, ...] = ("pg_dump", "pg_restore")
) -> PgClient:
    return PgClient(
        which=_which(*tools),
        runner=runner,
        allow_docker_fallback=False,
    )


def _docker_client(runner: RecordingRunner, compose: Path) -> PgClient:
    return PgClient(
        which=_which("docker"),
        runner=runner,
        compose_file=compose,
        allow_docker_fallback=True,
    )


class TestCreate:
    async def test_writes_an_atomic_private_utc_named_dump(self, tmp_path: Path) -> None:
        pg = FakePgClient()
        service = _service(tmp_path, pg_client=pg)

        backup = await service.create()

        backups_dir = tmp_path / "store" / "backups"
        assert isinstance(backup, Backup)
        assert backup.path.parent == backups_dir
        assert BACKUP_NAME.match(backup.path.name)
        assert backup.path.exists()
        assert backup.size_bytes == 32
        assert backup.created_at.tzinfo == UTC

        assert stat.S_IMODE(backup.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(backups_dir.stat().st_mode) == 0o700
        # No partial file survives a successful run.
        assert list(backups_dir.glob("*.tmp")) == []
        # The dump went to a temp path in the same directory, then was renamed.
        ((_, dump_target),) = pg.dump_calls
        assert dump_target.parent == backups_dir
        assert dump_target != backup.path

    async def test_name_collisions_get_a_numeric_suffix(self, tmp_path: Path) -> None:
        service = _service(tmp_path)

        first = await service.create()
        second = await service.create()

        assert first.path != second.path
        assert first.path.exists() and second.path.exists()
        assert [b.path for b in service.list_backups()] == [second.path, first.path]

    async def test_dump_failure_leaves_no_file_behind(self, tmp_path: Path) -> None:
        service = _service(tmp_path, pg_client=FakePgClient(error=BackupError("boom")))

        with pytest.raises(BackupError, match="boom"):
            await service.create()

        backups_dir = tmp_path / "store" / "backups"
        assert list(backups_dir.iterdir()) == []

    async def test_reason_is_logged_without_the_url(self, tmp_path: Path, caplog) -> None:
        service = _service(tmp_path)

        with caplog.at_level(logging.INFO):
            backup = await service.create(reason="pre-migration")

        assert str(backup.path) in caplog.text
        assert "pre-migration" in caplog.text
        assert "s3cret" not in caplog.text
        assert DATABASE_URL not in caplog.text


class TestRetention:
    async def test_keeps_the_newest_n_and_the_just_created_file(self, tmp_path: Path) -> None:
        service = _service(tmp_path, retention=2)

        created = [await service.create() for _ in range(4)]

        remaining = sorted(p.name for p in service.backups_dir.glob("*.dump"))
        assert len(remaining) == 2
        assert created[-1].path.name in remaining
        assert created[-2].path.name in remaining
        assert [b.path for b in service.list_backups()] == [
            created[-1].path,
            created[-2].path,
        ]

    async def test_zero_retention_keeps_everything(self, tmp_path: Path) -> None:
        service = _service(tmp_path, retention=0)

        for _ in range(4):
            await service.create()

        assert len(list(service.backups_dir.glob("*.dump"))) == 4

    async def test_delete_errors_are_logged_not_fatal(self, tmp_path: Path, caplog) -> None:
        service = _service(tmp_path, retention=1)
        old = service.backups_dir / "cortex-20200101T000000Z.dump"
        old.mkdir()  # a directory with a .dump name cannot be unlinked

        with caplog.at_level(logging.WARNING):
            backup = await service.create()

        assert backup.path.exists()
        assert old.exists()
        assert "could not delete" in caplog.text


class TestResolveAndList:
    async def test_resolves_an_id_a_file_name_and_a_path(self, tmp_path: Path) -> None:
        service = _service(tmp_path)
        backup = await service.create()
        backup_id = backup.path.stem

        assert service.resolve(backup_id) == backup.path
        assert service.resolve(backup.path.name) == backup.path
        assert service.resolve(str(backup.path)) == backup.path

    def test_a_missing_backup_raises(self, tmp_path: Path) -> None:
        service = _service(tmp_path)

        with pytest.raises(BackupError, match="not found"):
            service.resolve("cortex-20200101T000000Z")

    async def test_list_is_newest_first(self, tmp_path: Path) -> None:
        service = _service(tmp_path)
        first = await service.create()
        second = await service.create()

        assert [backup.path for backup in service.list_backups()] == [
            second.path,
            first.path,
        ]


class TestRestore:
    async def test_refuses_while_the_database_is_in_use(self, tmp_path: Path) -> None:
        pg = FakePgClient()
        factory = FakeConnectionFactory(other_connections=2)
        service = _service(tmp_path, pg_client=pg, connection_factory=factory)
        backup = await service.create()

        with pytest.raises(BackupError, match="database is in use; stop the app or pass --force"):
            await service.restore(backup.path)

        assert factory.connection.closed is True
        assert "pg_stat_activity" in (factory.connection.query or "")
        assert "current_database()" in (factory.connection.query or "")
        assert "pid <> pg_backend_pid()" in (factory.connection.query or "")
        assert pg.restore_calls == []

    async def test_force_skips_the_in_use_check(self, tmp_path: Path) -> None:
        pg = FakePgClient()
        factory = FakeConnectionFactory(other_connections=2)
        service = _service(tmp_path, pg_client=pg, connection_factory=factory)
        backup = await service.create()

        restored = await service.restore(backup.path, force=True)

        assert factory.calls == []
        assert pg.restore_calls == [(DATABASE_URL, backup.path)]
        assert restored.path == backup.path

    async def test_restores_when_the_check_finds_no_other_connection(
        self, tmp_path: Path
    ) -> None:
        pg = FakePgClient()
        factory = FakeConnectionFactory(other_connections=0)
        service = _service(tmp_path, pg_client=pg, connection_factory=factory)
        backup = await service.create()

        await service.restore(backup.path)

        assert factory.connection.closed is True
        assert pg.restore_calls == [(DATABASE_URL, backup.path)]

    async def test_closes_the_check_connection_when_the_query_fails(
        self, tmp_path: Path
    ) -> None:
        class BrokenConnection(FakeConnection):
            async def fetchval(self, query: str) -> int:
                raise RuntimeError("query failed")

        class BrokenFactory(FakeConnectionFactory):
            async def __call__(self, database_url: str) -> FakeConnection:
                self.calls.append(database_url)
                self.connection = BrokenConnection(0)
                return self.connection

        pg = FakePgClient()
        factory = BrokenFactory()
        service = _service(tmp_path, pg_client=pg, connection_factory=factory)
        backup = await service.create()

        with pytest.raises(RuntimeError, match="query failed"):
            await service.restore(backup.path)

        assert factory.connection.closed is True
        assert pg.restore_calls == []


class TestUrlSanitizing:
    def test_removes_the_password_and_keeps_the_rest(self) -> None:
        assert (
            sanitize_database_url(DATABASE_URL)
            == "postgresql://cortex@localhost:5433/cortex"
        )

    def test_a_url_without_a_password_is_unchanged(self) -> None:
        url = "postgresql://cortex@localhost:5433/cortex"

        assert sanitize_database_url(url) == url

    def test_percent_encoded_password_is_decoded_for_pgpassword(self) -> None:
        assert (
            sanitize_database_url("postgresql://user:p%40ss@db:5432/cortex")
            == "postgresql://user@db:5432/cortex"
        )


class TestHostTransport:
    async def test_dump_uses_the_private_url_and_pgpassword_env(self, tmp_path: Path) -> None:
        runner = RecordingRunner(stdout=b"dump")
        client = _host_client(runner)
        target = tmp_path / "out.dump"

        await client.dump(DATABASE_URL, target)

        ((argv, kwargs),) = runner.calls
        assert argv[0] == "/usr/bin/pg_dump"
        assert "--format=custom" in argv
        assert "--no-owner" in argv
        assert "--no-privileges" in argv
        assert argv[argv.index("--file") + 1] == str(target)
        assert argv[argv.index("--dbname") + 1] == (
            "postgresql://cortex@localhost:5433/cortex"
        )
        assert "s3cret" not in " ".join(argv)
        assert kwargs["env"]["PGPASSWORD"] == "s3cret"

    async def test_dump_without_a_password_sets_no_pgpassword(self, tmp_path: Path) -> None:
        runner = RecordingRunner()
        client = _host_client(runner)
        target = tmp_path / "out.dump"

        await client.dump("postgresql://cortex@localhost:5433/cortex", target)

        ((argv, kwargs),) = runner.calls
        assert "PGPASSWORD" not in kwargs["env"]
        assert "s3cret" not in " ".join(argv)

    async def test_restore_argv_is_clean_and_if_exists(self, tmp_path: Path) -> None:
        runner = RecordingRunner()
        client = _host_client(runner)
        source = tmp_path / "in.dump"
        source.write_bytes(b"dump")

        await client.restore(DATABASE_URL, source)

        ((argv, kwargs),) = runner.calls
        assert argv[0] == "/usr/bin/pg_restore"
        assert "--clean" in argv
        assert "--if-exists" in argv
        assert "--no-owner" in argv
        assert "--no-privileges" in argv
        assert argv[argv.index("--dbname") + 1] == (
            "postgresql://cortex@localhost:5433/cortex"
        )
        assert str(source) in argv
        assert "s3cret" not in " ".join(argv)
        assert kwargs["env"]["PGPASSWORD"] == "s3cret"

    async def test_failure_is_an_error_without_the_password(
        self, tmp_path: Path, caplog
    ) -> None:
        runner = RecordingRunner(returncode=1, stderr=b"connection failed for s3cret")
        client = _host_client(runner)

        with caplog.at_level(logging.DEBUG):
            with pytest.raises(BackupError) as excinfo:
                await client.dump(DATABASE_URL, tmp_path / "out.dump")

        assert "pg_dump failed" in str(excinfo.value)
        assert "s3cret" not in str(excinfo.value)
        assert "s3cret" not in caplog.text


class TestDockerTransport:
    async def test_dump_streams_stdout_from_the_compose_service(self, tmp_path: Path) -> None:
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services: {}\n", encoding="utf-8")
        runner = RecordingRunner(stdout=b"docker-dump")
        client = _docker_client(runner, compose)
        target = tmp_path / "out.dump"

        await client.dump(DATABASE_URL, target)

        ((argv, kwargs),) = runner.calls
        assert argv[:6] == (
            "/usr/bin/docker",
            "compose",
            "-f",
            str(compose),
            "exec",
            "-T",
        )
        assert argv[argv.index("-e") + 1] == "PGPASSWORD"
        assert argv[argv.index("postgres") + 1] == "pg_dump"
        assert "--format=custom" in argv
        assert argv[argv.index("-U") + 1] == "cortex"
        assert argv[argv.index("-d") + 1] == "cortex"
        assert "s3cret" not in " ".join(argv)
        assert kwargs["env"]["PGPASSWORD"] == "s3cret"
        assert target.read_bytes() == b"docker-dump"

    async def test_restore_streams_the_file_on_stdin(self, tmp_path: Path) -> None:
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services: {}\n", encoding="utf-8")
        runner = RecordingRunner()
        client = _docker_client(runner, compose)
        source = tmp_path / "in.dump"
        source.write_bytes(b"dump-bytes")

        await client.restore(DATABASE_URL, source)

        ((argv, _),) = runner.calls
        assert argv[argv.index("postgres") + 1] == "pg_restore"
        assert "--clean" in argv
        assert "--if-exists" in argv
        assert "s3cret" not in " ".join(argv)
        assert runner.process is not None
        assert runner.process.stdin_data == b"dump-bytes"

    async def test_no_password_adds_no_env_forwarding_flag(self, tmp_path: Path) -> None:
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services: {}\n", encoding="utf-8")
        runner = RecordingRunner(stdout=b"dump")
        client = _docker_client(runner, compose)

        await client.dump("postgresql://cortex@postgres:5432/cortex", tmp_path / "out.dump")

        ((argv, kwargs),) = runner.calls
        assert "-e" not in argv
        assert "PGPASSWORD" not in kwargs["env"]


class TestTransportSelection:
    async def test_no_binary_and_no_fallback_fails_loudly(self, tmp_path: Path) -> None:
        client = PgClient(which=_which(), runner=RecordingRunner(), allow_docker_fallback=False)

        with pytest.raises(BackupError, match="pg_dump"):
            await client.dump(DATABASE_URL, tmp_path / "out.dump")

    async def test_remote_host_without_a_binary_fails_loudly(self, tmp_path: Path) -> None:
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services: {}\n", encoding="utf-8")
        client = PgClient(
            which=_which("docker"),
            runner=RecordingRunner(),
            compose_file=compose,
        )

        with pytest.raises(BackupError, match="not the local compose Postgres"):
            await client.dump(
                "postgresql://cortex:s3cret@db.example.com:5432/cortex",
                tmp_path / "out.dump",
            )

    async def test_no_docker_binary_fails_loudly(self, tmp_path: Path) -> None:
        client = PgClient(which=_which(), runner=RecordingRunner())

        with pytest.raises(BackupError, match="Docker is not available"):
            await client.dump(DATABASE_URL, tmp_path / "out.dump")

    async def test_missing_compose_file_fails_loudly(self, tmp_path: Path) -> None:
        client = PgClient(
            which=_which("docker"),
            runner=RecordingRunner(),
            compose_file=tmp_path / "missing.yml",
        )

        with pytest.raises(BackupError, match="compose file not found"):
            await client.dump(DATABASE_URL, tmp_path / "out.dump")

    async def test_spawn_failure_is_a_backup_error(self, tmp_path: Path) -> None:
        async def broken_runner(*argv: str, **kwargs: object) -> FakeProcess:
            raise FileNotFoundError("pg_dump vanished")

        client = PgClient(
            which=_which("pg_dump"),
            runner=broken_runner,
            allow_docker_fallback=False,
        )

        with pytest.raises(BackupError, match="could not start"):
            await client.dump(DATABASE_URL, tmp_path / "out.dump")
