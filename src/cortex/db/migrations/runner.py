"""Database migrations runner."""

import logging
from pathlib import Path

from cortex.config.models import Settings
from cortex.db.pool import get_pool
from cortex.safety.backup import BackupService, PgClient

logger = logging.getLogger(__name__)


def _migrations_dir() -> Path:
    """Resolve the migrations directory.

    Canonical location is the repo-root ``migrations/`` (works in local
    checkouts and in the Docker image, where both the repo root and the
    ``src/migrations`` copy exist). Falls back to ``src/migrations`` for
    layouts that only ship the copied directory.
    """
    repo_root = Path(__file__).parents[4]
    candidates = (repo_root / "migrations", Path(__file__).parents[3] / "migrations")
    return next((c for c in candidates if c.exists()), candidates[0])


MIGRATIONS_DIR = _migrations_dir()


async def run_migrations(settings: Settings | None = None) -> None:
    """
    Run all pending database migrations.

    Migrations are SQL files in the migrations directory
    named with numeric prefixes (e.g., 001_initial.sql).

    Creates the schema_migrations table if it doesn't exist.

    When migrations are pending and ``safety.backup_on_migration`` is set, a
    pre-migration dump is taken first (ADR-0021). A failed dump aborts by
    default, before any migration runs; ``require_backup_before_migration``
    turns that into a logged warning.
    """
    if settings is None:
        from cortex.config.loader import get_settings

        settings = get_settings()

    pool = await get_pool()

    async with pool.acquire() as conn:
        # Create migrations table if not exists
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version VARCHAR(255) PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT NOW()
            )
        """)

        # Get already applied migrations
        applied = await conn.fetch("SELECT version FROM schema_migrations")
        applied_versions = {row["version"] for row in applied}

        logger.info(f"Found {len(applied_versions)} applied migrations")

        if not MIGRATIONS_DIR.exists():
            logger.warning(f"Migrations directory not found: {MIGRATIONS_DIR}")
            return

        migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        pending = [f for f in migration_files if f.stem not in applied_versions]

        if pending and settings.safety.backup_on_migration:
            await _pre_migration_backup(settings)

        for migration_file in pending:
            version = migration_file.stem  # e.g., "001_initial"

            logger.info(f"Applying migration: {version}")

            # Read and execute migration
            sql = migration_file.read_text()

            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES ($1)", version
                )

            logger.info(f"Migration applied: {version}")


async def _pre_migration_backup(settings: Settings) -> None:
    """Dump before pending migrations; fail closed unless configured otherwise.

    Inside the Cortex image there is no Docker CLI to fall back to, so the
    binary is required: ``allow_docker_fallback=False`` makes a missing
    ``pg_dump`` a loud failure rather than a silent skip.
    """
    try:
        service = BackupService(
            settings.safety,
            settings.database.database_url,
            pg_client=PgClient(allow_docker_fallback=False),
        )
        backup = await service.create(reason="pre-migration")
    except Exception as exc:
        if settings.safety.require_backup_before_migration:
            raise RuntimeError(
                "pre-migration backup failed; refusing to apply migrations"
            ) from exc
        logger.error("Pre-migration backup failed; continuing: %s", exc)
        return

    logger.info("Pre-migration backup: %s", backup.path)


async def create_migration(name: str, migrations_dir: Path | None = None) -> Path:
    """
    Create a new migration file with the next sequence number.

    Args:
        name: Descriptive name for the migration
        migrations_dir: Optional custom migrations directory

    Returns:
        Path to the created migration file
    """
    dir_path = migrations_dir or MIGRATIONS_DIR

    if not dir_path.exists():
        dir_path.mkdir(parents=True, exist_ok=True)

    # Find highest existing version
    existing = sorted(dir_path.glob("*.sql"))
    if existing:
        last = existing[-1].stem
        try:
            next_num = int(last.split("_")[0]) + 1
        except ValueError:
            next_num = 1
    else:
        next_num = 1

    version = f"{next_num:03d}_{name}"
    file_path = dir_path / f"{version}.sql"

    # Create file with placeholder
    file_path.write_text(
        f"-- Migration: {version}\n-- Created: {__import__('datetime').datetime.utcnow()}\n\n"
    )

    logger.info(f"Created migration file: {file_path}")
    return file_path
