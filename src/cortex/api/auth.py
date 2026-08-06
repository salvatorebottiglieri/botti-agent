"""API Key authentication module."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

# Token header name
TOKEN_HEADER = APIKeyHeader(name="Authorization", auto_error=False)

# In-memory cache of valid token hashes (loaded at startup)
_token_cache: dict[str, dict] = {}
_cache_initialized = False


def _hash_token(token: str) -> str:
    """Hash a token using SHA-256."""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_token() -> tuple[str, str]:
    """
    Generate a new API token.

    Returns:
        Tuple of (raw_token, hashed_token)
        The raw_token is shown once to the user and cannot be recovered.
    """
    raw = f"ctx_{secrets.token_urlsafe(32)}"
    hashed = _hash_token(raw)
    return raw, hashed


async def get_api_key(
    token_header: Annotated[str | None, Depends(TOKEN_HEADER)],
) -> str:
    """
    Dependency to validate API key from Authorization header.

    Usage in routes:
        @app.get("/protected")
        async def protected(key: str = Depends(get_api_key)):
            ...
    """
    global _token_cache

    if not token_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthorized", "detail": "Missing Authorization header"},
        )

    # Extract token from "Bearer <token>"
    parts = token_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthorized", "detail": "Invalid Authorization header format"},
        )

    token = parts[1]
    token_hash = _hash_token(token)

    # Check cache first
    if token_hash in _token_cache:
        return token_hash

    # Fall back to the database: the in-memory cache is loaded at startup,
    # so tokens created later (e.g. via `cortex token:create`) would be
    # rejected forever otherwise. Cache hits on success.
    try:
        from cortex.api.dependencies import get_db_pool

        db = await get_db_pool()
        async with db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, key_hash, name FROM api_keys WHERE key_hash = $1 AND revoked_at IS NULL",
                token_hash,
            )
            if row is not None:
                _token_cache[row["key_hash"]] = {
                    "id": str(row["id"]),
                    "name": row["name"],
                }
                return token_hash
    except Exception:
        # DB unavailable: reject rather than crash the request
        pass

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": "unauthorized", "detail": "Invalid API token"},
    )


async def load_token_cache(get_db_pool):
    """
    Load valid tokens from database into memory cache.

    Called at startup.
    """
    global _token_cache, _cache_initialized

    if _cache_initialized:
        return

    try:
        db = await get_db_pool()
        async with db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, key_hash, name FROM api_keys WHERE revoked_at IS NULL"
            )
            for row in rows:
                _token_cache[row["key_hash"]] = {
                    "id": str(row["id"]),
                    "name": row["name"],
                }
        _cache_initialized = True
    except Exception:
        # If DB not ready, skip for now
        pass


async def create_token(
    name: str,
    get_db_pool,
) -> tuple[str, str, str]:
    """
    Create a new API token and store in database.

    Args:
        name: Token name/description
        get_db_pool: Function that returns the DB pool

    Returns:
        Tuple of (raw_token, hashed_token, db_id)
    """
    raw, hashed = generate_token()

    try:
        db = await get_db_pool()
        async with db.acquire() as conn:
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

        # Add to cache
        _token_cache[hashed] = {
            "id": str(row["id"]),
            "name": name,
        }

        return raw, hashed, str(row["id"])
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "internal_error", "detail": f"Failed to create token: {str(e)}"},
        )


async def revoke_token(
    token_hash: str,
    get_db_pool,
) -> bool:
    """
    Revoke an API token.

    Args:
        token_hash: The hashed token to revoke
        get_db_pool: Function that returns the DB pool

    Returns:
        True if revoked, False if not found
    """
    try:
        db = await get_db_pool()
        async with db.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE api_keys 
                SET revoked_at = $1 
                WHERE key_hash = $2 AND revoked_at IS NULL
                """,
                datetime.now(UTC),
                token_hash,
            )

        if result == "UPDATE 1":
            # Remove from cache
            _token_cache.pop(token_hash, None)
            return True
        return False
    except Exception:
        return False


async def check_token_exists(get_db_pool) -> bool:
    """
    Check if any tokens exist in the database.

    Used to determine if /admin/token/init should be allowed.
    """
    try:
        db = await get_db_pool()
        async with db.acquire() as conn:
            row = await conn.fetchrow("SELECT COUNT(*) as cnt FROM api_keys")
            return row["cnt"] > 0
    except Exception:
        return False


async def list_tokens(get_db_pool) -> list[dict]:
    """
    List all API tokens (without secrets).
    """
    try:
        db = await get_db_pool()
        async with db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, name, created_at, last_used_at, revoked_at
                FROM api_keys
                ORDER BY created_at DESC
                """
            )
            return [
                {
                    "id": str(row["id"]),
                    "name": row["name"],
                    "created_at": row["created_at"],
                    "last_used_at": row["last_used_at"],
                    "revoked": row["revoked_at"] is not None,
                }
                for row in rows
            ]
    except Exception:
        return []
