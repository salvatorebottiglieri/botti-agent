"""PostgreSQL backups and the external safety store (ADR-0021, issue #146).

Two layers:

* :class:`PgClient` is the transport. It prefers ``pg_dump``/``pg_restore`` on
  ``PATH`` and falls back to ``docker compose exec`` for the local compose
  Postgres. The password travels in ``PGPASSWORD`` — never on a command line,
  never in the sanitized URL, never in an error message.
* :class:`BackupService` owns the store: atomic custom-format dumps, retention,
  id/path resolution, and the in-use guard before a restore.

The store lives under ``SAFETY_STORE_DIR`` (``~/.local/share/cortex``), outside
the repo and every Docker volume: a backup inside the volume dies with
``docker compose down -v``, which is the incident this net exists for.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

import asyncpg

from cortex.config.safety import SafetySettings

logger = logging.getLogger(__name__)

#: Hosts that mean "the compose Postgres" for the Docker fallback.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "postgres"})

#: ``cortex-YYYYmmddTHHMMSSZ.dump`` with an optional ``-N`` collision suffix.
_BACKUP_NAME = re.compile(r"^cortex-(\d{8}T\d{6}Z)(?:-(\d+))?\.dump$")

Runner = Callable[..., Awaitable[Any]]
Which = Callable[[str], str | None]


class BackupError(RuntimeError):
    """A backup or restore could not be performed."""


@dataclass(frozen=True, slots=True)
class Backup:
    """One dump in the store."""

    path: Path
    size_bytes: int
    created_at: datetime


def sanitize_database_url(database_url: str) -> str:
    """Return the URL without its password component.

    Safe to print and to put on a command line: only the userinfo password is
    removed; user, host, port, database and query parameters are preserved.
    """
    parts = urlsplit(database_url)
    userinfo, separator, hostinfo = parts.netloc.rpartition("@")
    if not separator:
        return database_url

    username = userinfo.split(":", 1)[0]
    netloc = f"{username}@{hostinfo}" if username else hostinfo
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


@dataclass(frozen=True, slots=True)
class _Connection:
    user: str
    password: str | None
    database: str
    host: str


def _parse_connection(database_url: str) -> _Connection:
    parts = urlsplit(database_url)
    return _Connection(
        user=unquote(parts.username) if parts.username else "postgres",
        password=unquote(parts.password) if parts.password else None,
        database=unquote(parts.path.lstrip("/")) or "cortex",
        host=(parts.hostname or "localhost").lower(),
    )


def _redact(text: str, password: str | None) -> str:
    if password:
        return text.replace(password, "***")
    return text


class PgClient:
    """Run ``pg_dump``/``pg_restore`` on the host or through compose.

    ``which`` and ``runner`` are constructor seams so tests never execute a
    real binary. ``allow_docker_fallback=False`` is the in-container mode: a
    missing binary must fail, because there is no compose CLI to fall back to.
    """

    def __init__(
        self,
        *,
        which: Which = shutil.which,
        runner: Runner = asyncio.create_subprocess_exec,
        compose_file: Path | None = None,
        allow_docker_fallback: bool = True,
    ) -> None:
        self._which = which
        self._runner = runner
        self._compose_file = compose_file or (
            Path(__file__).resolve().parents[3] / "docker-compose.yml"
        )
        self._allow_docker_fallback = allow_docker_fallback

    async def dump(self, database_url: str, target: Path) -> None:
        """Write a custom-format dump of ``database_url`` to ``target``."""
        binary = self._which("pg_dump")
        if binary is not None:
            await self._dump_host(binary, database_url, target)
            return

        docker = self._docker_binary("pg_dump", database_url)
        await self._dump_docker(docker, database_url, target)

    async def restore(self, database_url: str, source: Path) -> None:
        """Restore ``source`` into ``database_url`` with ``--clean --if-exists``."""
        binary = self._which("pg_restore")
        if binary is not None:
            await self._restore_host(binary, database_url, source)
            return

        docker = self._docker_binary("pg_restore", database_url)
        await self._restore_docker(docker, database_url, source)

    def _docker_binary(self, tool: str, database_url: str) -> str:
        """Return the docker binary, or raise why the fallback is unavailable."""
        connection = _parse_connection(database_url)

        if not self._allow_docker_fallback:
            raise BackupError(
                f"{tool} not found on PATH; install the PostgreSQL client tools"
            )
        if connection.host not in _LOCAL_HOSTS:
            raise BackupError(
                f"{tool} not found on PATH and the database host "
                f"{connection.host!r} is not the local compose Postgres; "
                "install the PostgreSQL client tools"
            )

        docker = self._which("docker")
        if docker is None:
            raise BackupError(
                f"{tool} not found on PATH and Docker is not available; "
                "install the PostgreSQL client tools"
            )
        if not self._compose_file.is_file():
            raise BackupError(f"docker compose file not found at {self._compose_file}")
        return docker

    @staticmethod
    def _env(password: str | None) -> dict[str, str]:
        """The password travels in the environment, never in argv.

        An ambient ``PGPASSWORD`` is dropped when the URL has none: the URL is
        the single source of credentials, and a stale shell value must not leak
        into a dump of a different database.
        """
        env = dict(os.environ)
        if password is None:
            env.pop("PGPASSWORD", None)
        else:
            env["PGPASSWORD"] = password
        return env

    async def _run(
        self,
        tool: str,
        argv: list[str],
        *,
        env: dict[str, str],
        password: str | None,
        stdin: bytes | None = None,
    ) -> bytes:
        kwargs: dict[str, Any] = {
            "env": env,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        if stdin is not None:
            kwargs["stdin"] = asyncio.subprocess.PIPE

        try:
            process = await self._runner(*argv, **kwargs)
        except OSError as exc:
            raise BackupError(f"{tool} could not start: {exc}") from exc
        stdout, stderr = await process.communicate(stdin)
        if process.returncode != 0:
            detail = _redact(stderr.decode(errors="replace").strip(), password)
            suffix = f": {detail}" if detail else ""
            raise BackupError(f"{tool} failed ({process.returncode}){suffix}")
        return bytes(stdout)

    async def _dump_host(self, binary: str, database_url: str, target: Path) -> None:
        connection = _parse_connection(database_url)
        argv = [
            binary,
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--file",
            str(target),
            "--dbname",
            sanitize_database_url(database_url),
        ]
        await self._run(
            "pg_dump",
            argv,
            env=self._env(connection.password),
            password=connection.password,
        )

    async def _restore_host(self, binary: str, database_url: str, source: Path) -> None:
        connection = _parse_connection(database_url)
        argv = [
            binary,
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            "--dbname",
            sanitize_database_url(database_url),
            str(source),
        ]
        await self._run(
            "pg_restore",
            argv,
            env=self._env(connection.password),
            password=connection.password,
        )

    async def _dump_docker(self, docker: str, database_url: str, target: Path) -> None:
        connection = _parse_connection(database_url)
        argv = self._compose_argv(docker, "pg_dump", connection)
        stdout = await self._run(
            "pg_dump",
            argv,
            env=self._env(connection.password),
            password=connection.password,
        )
        target.write_bytes(stdout)

    async def _restore_docker(
        self, docker: str, database_url: str, source: Path
    ) -> None:
        connection = _parse_connection(database_url)
        argv = self._compose_argv(docker, "pg_restore", connection)
        await self._run(
            "pg_restore",
            argv,
            env=self._env(connection.password),
            password=connection.password,
            stdin=source.read_bytes(),
        )

    def _compose_argv(
        self, docker: str, tool: str, connection: _Connection
    ) -> list[str]:
        argv = [
            docker,
            "compose",
            "-f",
            str(self._compose_file),
            "exec",
            "-T",
        ]
        if connection.password is not None:
            # ``-e PGPASSWORD`` forwards the value from this process's env; the
            # secret itself never appears on the command line.
            argv += ["-e", "PGPASSWORD"]
        argv += [
            "postgres",
            tool,
        ]
        if tool == "pg_dump":
            argv += ["--format=custom", "--no-owner", "--no-privileges"]
        else:
            argv += ["--clean", "--if-exists", "--no-owner", "--no-privileges"]
        argv += ["-U", connection.user, "-d", connection.database]
        return argv


class BackupService:
    """The safety store's backup/restore API.

    ``connection_factory`` is the ``asyncpg.connect`` seam used only by the
    restore in-use check; dumps never open a database connection through it.
    """

    def __init__(
        self,
        settings: SafetySettings,
        database_url: str,
        *,
        store_dir: Path | None = None,
        pg_client: PgClient | None = None,
        connection_factory: Callable[[str], Awaitable[Any]] = asyncpg.connect,
    ) -> None:
        self._settings = settings
        self._database_url = database_url
        self._pg = pg_client or PgClient()
        self._connection_factory = connection_factory
        self.backups_dir = Path(store_dir or settings.store_dir) / "backups"
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        self.backups_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.backups_dir.chmod(0o700)
        except OSError as exc:  # a mounted store may refuse the chmod
            logger.warning("could not set mode 0700 on %s: %s", self.backups_dir, exc)

    async def create(self, *, reason: str | None = None) -> Backup:
        """Dump the database atomically, prune by retention, return the backup."""
        self._ensure_dir()
        created_at = datetime.now(UTC)
        final = self._next_path(created_at)

        fd, temp_name = tempfile.mkstemp(
            prefix=".cortex-", suffix=".tmp", dir=self.backups_dir
        )
        os.close(fd)
        temp = Path(temp_name)
        size = 0
        try:
            await self._pg.dump(self._database_url, temp)
            if not temp.is_file():
                raise BackupError("pg_dump produced no file")
            temp.chmod(0o600)
            size = temp.stat().st_size
            os.replace(temp, final)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise

        logger.info("Created backup %s%s", final, f" ({reason})" if reason else "")
        self._prune(keep=final)
        return Backup(final, size, created_at)

    def list_backups(self) -> list[Backup]:
        """Every dump in the store, newest first (name order, then mtime)."""
        if not self.backups_dir.exists():
            return []

        backups: list[Backup] = []
        for path in self.backups_dir.glob("*.dump"):
            try:
                stat = path.stat()
            except OSError:  # removed between glob and stat
                continue
            backups.append(
                Backup(path, stat.st_size, self._created_at(path, stat.st_mtime))
            )
        backups.sort(key=lambda backup: self._sort_key(backup.path), reverse=True)
        return backups

    def resolve(self, id_or_path: str) -> Path:
        """Resolve a backup id, file name or explicit path to its file."""
        candidate = Path(id_or_path).expanduser()
        if candidate.exists():
            return candidate

        by_suffix = self.backups_dir / f"{id_or_path}.dump"
        if by_suffix.exists():
            return by_suffix

        raw = self.backups_dir / id_or_path
        if raw.exists():
            return raw

        raise BackupError(f"backup not found: {id_or_path}")

    async def restore(self, id_or_path: str, *, force: bool = False) -> Backup:
        """Restore a backup, refusing while the target database is in use."""
        path = self.resolve(id_or_path)

        if not force:
            connection = await self._connection_factory(self._database_url)
            try:
                in_use = await connection.fetchval(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND pid <> pg_backend_pid()"
                )
            finally:
                await connection.close()
            if in_use:
                raise BackupError(
                    "database is in use; stop the app or pass --force"
                )

        await self._pg.restore(self._database_url, path)
        stat = path.stat()
        return Backup(
            path,
            stat.st_size,
            datetime.fromtimestamp(stat.st_mtime, UTC),
        )

    def _next_path(self, created_at: datetime) -> Path:
        """Never reuse a name: retention may have freed a suffix, but a reused
        base name would sort older than the suffixed files it sits beside."""
        timestamp = f"{created_at:%Y%m%dT%H%M%SZ}"
        base = f"cortex-{timestamp}"
        highest = -1
        for path in self.backups_dir.glob(f"{base}*.dump"):
            match = _BACKUP_NAME.match(path.name)
            if match is not None and match.group(1) == timestamp:
                highest = max(highest, int(match.group(2) or 0))

        if highest < 0:
            return self.backups_dir / f"{base}.dump"
        return self.backups_dir / f"{base}-{highest + 1}.dump"

    def _prune(self, *, keep: Path) -> None:
        retention = self._settings.backup_retention
        if retention == 0:
            return

        for backup in self.list_backups()[retention:]:
            if backup.path == keep:
                continue
            try:
                backup.path.unlink()
            except OSError as exc:
                logger.warning("could not delete backup %s: %s", backup.path, exc)

    @staticmethod
    def _sort_key(path: Path) -> tuple[int, str, int, str]:
        match = _BACKUP_NAME.match(path.name)
        if match is None:
            return (0, "", 0, path.name)
        return (1, match.group(1), int(match.group(2) or 0), path.name)

    @staticmethod
    def _created_at(path: Path, mtime: float) -> datetime:
        match = _BACKUP_NAME.match(path.name)
        if match is not None:
            return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(
                tzinfo=UTC
            )
        return datetime.fromtimestamp(mtime, UTC)
