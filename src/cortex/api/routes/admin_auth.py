"""Admin auth routes - token management."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, status

from cortex.api.auth import (
    check_token_exists,
    create_token,
    get_api_key,
    list_tokens,
)
from cortex.api.dependencies import get_db_pool
from cortex.api.schemas import TokenCreateRequest, TokenListItem, TokenResponse

if TYPE_CHECKING:
    import asyncpg

router = APIRouter(prefix="/admin", tags=["admin", "auth"])


@router.post(
    "/token/init",
    summary="Initialize first token",
    description="Creates the first API token. Only works if no tokens exist.",
    openapi_extra={
        "security": [],  # No auth required for init
    },
)
async def init_first_token(
    request: TokenCreateRequest,
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> TokenResponse:
    """
    Initialize the first API token.

    Only works when no tokens exist in the database.
    Use this to bootstrap the system.
    """
    # Check if tokens already exist
    exists = await check_token_exists(db_pool)
    if exists:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "forbidden",
                "detail": "Tokens already exist. Use /admin/tokens to create more.",
            },
        )

    raw, hashed, db_id = await create_token(request.name, db_pool)

    return TokenResponse(
        token=raw,  # Shown only once!
        name=request.name,
        created_at=datetime.now(UTC),
    )


@router.post(
    "/tokens",
    summary="Create a new token",
    description="Creates a new API token.",
)
async def create_new_token(
    request: TokenCreateRequest,
    key: str = Depends(get_api_key),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> TokenResponse:
    """Create a new API token."""
    raw, hashed, db_id = await create_token(request.name, db_pool)

    return TokenResponse(
        token=raw,  # Shown only once!
        name=request.name,
        created_at=datetime.now(UTC),
    )


@router.get(
    "/tokens",
    summary="List all tokens",
    description="Returns all API tokens (without secrets).",
)
async def list_api_tokens(
    key: str = Depends(get_api_key),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> list[TokenListItem]:
    """List all API tokens."""
    tokens = await list_tokens(db_pool)
    return [
        TokenListItem(
            id=t["id"],
            name=t["name"],
            created_at=t["created_at"],
            last_used_at=t["last_used_at"],
            revoked=t["revoked"],
        )
        for t in tokens
    ]
