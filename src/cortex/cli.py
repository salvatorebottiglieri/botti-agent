"""CLI commands for Cortex."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC
from typing import Any

import typer

cli = typer.Typer(help="Cortex CLI commands")


class _AsyncpgRuleSession:
    """A bare asyncpg connection presented as the DbSession surface.

    PostgresRuleRepository only needs fetch/fetchrow/execute behind an async
    context manager. Wrapping the command's own connection this way lets the
    ordinary repository honour ``--db-url`` instead of going through the shared
    pool.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def __aenter__(self) -> _AsyncpgRuleSession:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        return None

    async def fetch(self, query: str, *args: Any) -> Any:
        return await self._conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args: Any) -> Any:
        return await self._conn.fetchrow(query, *args)

    async def execute(self, query: str, *args: Any) -> Any:
        return await self._conn.execute(query, *args)


def _database_url(db_url: str | None) -> str:
    """``--db-url`` wins; the database slice is only loaded when it is absent."""
    if db_url:
        return db_url

    from cortex.config.loader import get_settings

    return get_settings().database.database_url


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
            database_url = settings.database.database_url

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
        help="Retention window in days (defaults to the trace settings slice)",
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
                database_url = settings.database.database_url
            if retention_days is None:
                retention_days = settings.trace.retention_days

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


@cli.command("rules:list", help="List the active learned rules")
def rules_list(db_url: str | None = typer.Option(None, "--db-url", help="Database URL")) -> None:
    """List the rules that may enter the context (staging + definitive).

    Goes through RuleService.list_active, so the sweep also applies expiry,
    promotion and demotion before printing.
    """
    import asyncio

    from cortex.rules.repository import PostgresRuleRepository
    from cortex.rules.service import RuleService

    async def run() -> None:
        import asyncpg

        conn = await asyncpg.connect(_database_url(db_url))
        try:
            service = RuleService(
                PostgresRuleRepository(session_factory=lambda: _AsyncpgRuleSession(conn))
            )
            rules = await service.list_active()
        finally:
            await conn.close()

        if not rules:
            typer.echo("no rules")
            return
        for rule in rules:
            typer.echo(
                f"{rule.id}  {rule.state.value:<10}  {rule.confidence:.3f}  "
                f"{rule.condition} → {rule.action}"
            )

    asyncio.run(run())


@cli.command("rules:show", help="Show one learned rule with its recent uses")
def rules_show(
    rule_id: str = typer.Argument(..., help="Rule ID"),
    db_url: str | None = typer.Option(None, "--db-url", help="Database URL"),
) -> None:
    """Show a rule's fields, its last 10 uses, and what superseded it."""
    import asyncio

    from cortex.rules.repository import PostgresRuleRepository
    from cortex.rules.service import RuleService

    async def run() -> None:
        from uuid import UUID

        import asyncpg

        parsed = UUID(rule_id)
        conn = await asyncpg.connect(_database_url(db_url))
        try:
            repository = PostgresRuleRepository(
                session_factory=lambda: _AsyncpgRuleSession(conn)
            )
            rule = await RuleService(repository).get(parsed)
            if rule is None:
                typer.echo(f"rule not found: {rule_id}")
                raise typer.Exit(1)
            uses = await repository.list_uses(parsed)
        finally:
            await conn.close()

        typer.echo(f"id: {rule.id}")
        typer.echo(f"state: {rule.state.value}")
        typer.echo(f"confidence: {rule.confidence:.3f}")
        typer.echo(f"origin: {rule.origin.value}")
        typer.echo(f"condition: {rule.condition}")
        typer.echo(f"action: {rule.action}")
        if rule.rationale:
            typer.echo(f"rationale: {rule.rationale}")
        typer.echo(f"created: {rule.created_at.isoformat()}")
        if rule.archived_at is not None:
            typer.echo(f"archived: {rule.archived_at.isoformat()}")
        if rule.superseded_by is not None:
            typer.echo(f"superseded by: {rule.superseded_by}")
        typer.echo("recent uses:")
        if not uses:
            typer.echo("  (none)")
        for use in uses[-10:]:
            typer.echo(
                f"  {use.occurred_at.isoformat()}  {use.outcome.value:<16}  {use.situation}"
            )

    asyncio.run(run())


@cli.command("rules:archive", help="Archive a learned rule")
def rules_archive(
    rule_id: str = typer.Argument(..., help="Rule ID"),
    db_url: str | None = typer.Option(None, "--db-url", help="Database URL"),
) -> None:
    """Retire a rule. The user's explicit command prevails over the classifier."""
    import asyncio

    from cortex.rules.repository import PostgresRuleRepository
    from cortex.rules.service import RuleService

    async def run() -> None:
        from uuid import UUID

        import asyncpg

        parsed = UUID(rule_id)
        conn = await asyncpg.connect(_database_url(db_url))
        try:
            service = RuleService(
                PostgresRuleRepository(session_factory=lambda: _AsyncpgRuleSession(conn))
            )
            archived = await service.archive(parsed)
            if archived is None:
                typer.echo(f"rule not found: {rule_id}")
                raise typer.Exit(1)
        finally:
            await conn.close()

        typer.echo(f"archived {archived.id} ({archived.state.value})")

    asyncio.run(run())


@cli.command("version", help="Show Cortex version")
def version() -> None:
    """Show the installed Cortex version."""
    from cortex.config.loader import get_settings

    settings = get_settings()
    typer.echo(f"Cortex v{settings.version}")


if __name__ == "__main__":
    cli()
