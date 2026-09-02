"""CLI commands for Cortex."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC

import typer

cli = typer.Typer(help="Cortex CLI commands")


@cli.command("token:create", help="Create a new API token")
def token_create(
    name: str = typer.Argument(..., help="Name for this token"),
    db_url: str | None = typer.Option(None, "--db-url", help="Database URL"),
) -> None:
    """Create a new API token and store in database."""
    import asyncio
    from datetime import datetime

    import asyncpg

    async def create_token() -> None:
        # Get DB URL
        database_url = db_url
        if not database_url:
            from cortex.config.loader import get_settings

            settings = get_settings()
            database_url = settings.database_url

        # Connect to DB
        conn = await asyncpg.connect(database_url)

        try:
            # Generate token
            raw = f"ctx_{secrets.token_urlsafe(32)}"
            hashed = hashlib.sha256(raw.encode()).hexdigest()

            # Insert into DB
            row = await conn.fetchrow(
                """
                INSERT INTO api_keys (key_hash, name, created_at)
                VALUES ($1, $2, $3)
                RETURNING id
                """,
                hashed,
                name,
                datetime.now(UTC),
            )

            typer.echo("\n✅ Token created successfully!")
            typer.echo(f"\nToken ID: {row['id']}")
            typer.echo(f"Name: {name}")
            typer.echo("\n🔐 YOUR TOKEN (shown only once):\n")
            typer.echo(raw)
            typer.echo("\n⚠️  Save this token now. You cannot retrieve it again.")

        finally:
            await conn.close()

    asyncio.run(create_token())


@cli.command("traces:cleanup", help="Delete loop-event trace rows older than the retention window")
def traces_cleanup(
    db_url: str | None = typer.Option(None, "--db-url", help="Database URL"),
    days: int | None = typer.Option(
        None,
        "--days",
        min=1,
        help="Retention window in days (defaults to trace_retention_days setting)",
    ),
) -> None:
    """Delete loop_events rows older than now - retention window (issue #114 T4).

    Mirrors token:create's convention (nested async runner, direct asyncpg
    connection). Only loop_events is touched — never sessions or messages.
    Safe on an empty database and idempotent; prints the deleted count.
    """
    import asyncio

    async def cleanup() -> None:
        from datetime import UTC, datetime, timedelta

        import asyncpg

        # DB URL + retention: explicit flags win; settings are only loaded
        # (and required) for whatever is not given explicitly.
        database_url = db_url
        retention_days = days
        if not database_url or retention_days is None:
            from cortex.config.loader import get_settings

            settings = get_settings()
            if not database_url:
                database_url = settings.database_url
            if retention_days is None:
                retention_days = settings.trace_retention_days

        # Same predicate as TraceRepository.delete_older_than (strictly older
        # than the cutoff), mirrored on the CLI's own connection so --db-url is
        # honored instead of going through the shared pool.
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        conn = await asyncpg.connect(database_url)
        try:
            status = await conn.execute(
                "DELETE FROM loop_events WHERE created_at < $1", cutoff
            )
        finally:
            await conn.close()

        deleted = int(status.split()[-1])
        typer.echo(f"Deleted {deleted} expired loop event(s) (created_at < {cutoff.isoformat()}).")

    asyncio.run(cleanup())


@cli.command("version", help="Show Cortex version")
def version() -> None:
    """Show the installed Cortex version."""
    from cortex.config.loader import get_settings

    settings = get_settings()
    typer.echo(f"Cortex v{settings.version}")


if __name__ == "__main__":
    cli()
