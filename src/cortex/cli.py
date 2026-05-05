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


@cli.command("version", help="Show Cortex version")
def version() -> None:
    """Show the installed Cortex version."""
    from cortex.config.loader import get_settings

    settings = get_settings()
    typer.echo(f"Cortex v{settings.version}")


if __name__ == "__main__":
    cli()
