"""Database migrations runner."""

import logging
from pathlib import Path

from cortex.db.pool import get_pool

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent.parent.parent.parent / "migrations"


async def run_migrations() -> None:
    """
    Run all pending database migrations.

    Migrations are SQL files in the migrations directory
    named with numeric prefixes (e.g., 001_initial.sql).

    Creates the schema_migrations table if it doesn't exist.
    """
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

        # Find and apply pending migrations
        if MIGRATIONS_DIR.exists():
            migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))

            for migration_file in migration_files:
                version = migration_file.stem  # e.g., "001_initial"

                if version in applied_versions:
                    continue

                logger.info(f"Applying migration: {version}")

                # Read and execute migration
                sql = migration_file.read_text()

                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(
                        "INSERT INTO schema_migrations (version) VALUES ($1)", version
                    )

                logger.info(f"Migration applied: {version}")
        else:
            logger.warning(f"Migrations directory not found: {MIGRATIONS_DIR}")


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
