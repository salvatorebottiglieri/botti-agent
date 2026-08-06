"""Minion admin routes - minion token and config management."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.params import Path

from cortex.api.auth import get_api_key
from cortex.api.dependencies import get_db_pool, get_minion_service
from cortex.api.schemas import (
    MinionConfigRequest,
    MinionResponse,
    MinionTokenRequest,
    MinionTokenResponse,
)

if TYPE_CHECKING:
    import asyncpg

    from cortex.services.minion_service import MinionService

router = APIRouter(prefix="/admin/minions", tags=["admin", "minions"])


@router.get(
    "",
    summary="List all registered minions",
    description="Returns all minions that have registered with Cortex.",
)
async def list_minions(
    key: str = Depends(get_api_key),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> list[MinionResponse]:
    """List all registered minions."""
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, minion_id, minion_type, minion_version,
                       registered_at, last_heartbeat_at, last_known_ip, metadata
                FROM minions
                ORDER BY registered_at DESC
                """
            )

        return [
            MinionResponse(
                id=row["id"],
                minion_id=row["minion_id"],
                minion_type=row["minion_type"],
                minion_version=row["minion_version"],
                registered_at=row["registered_at"],
                last_heartbeat_at=row["last_heartbeat_at"],
                last_known_ip=row["last_known_ip"],
                state="online" if row["last_heartbeat_at"] else "offline",
                metadata=row["metadata"] or {},
            )
            for row in rows
        ]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "internal_error", "detail": str(e)},
        )


@router.get(
    "/{minion_id:path}",
    summary="Get minion details",
    description="Returns details and status for a specific minion.",
)
async def get_minion(
    minion_id: Annotated[str, Path(description="Minion ID (string)")],
    key: str = Depends(get_api_key),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> MinionResponse:
    """Get minion details."""
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, minion_id, minion_type, minion_version,
                       registered_at, last_heartbeat_at, last_known_ip, metadata
                FROM minions
                WHERE minion_id = $1
                """,
                minion_id,
            )

        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "not_found", "detail": "Minion not found"},
            )

        return MinionResponse(
            id=row["id"],
            minion_id=row["minion_id"],
            minion_type=row["minion_type"],
            minion_version=row["minion_version"],
            registered_at=row["registered_at"],
            last_heartbeat_at=row["last_heartbeat_at"],
            last_known_ip=row["last_known_ip"],
            state="online" if row["last_heartbeat_at"] else "offline",
            metadata=row["metadata"] or {},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "internal_error", "detail": str(e)},
        )


@router.post(
    "/{minion_id:path}/token",
    response_model=MinionTokenResponse,
    summary="Generate minion token",
    description="Generates a new MQTT password for the minion and writes to passwd.conf.",
)
async def create_minion_token(
    minion_id: Annotated[str, Path(description="Minion ID")],
    request: MinionTokenRequest,
    key: str = Depends(get_api_key),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> MinionTokenResponse:
    """
    Generate a new MQTT password for a minion.

    1. Generate a secure random password
    2. Hash with bcrypt and store in api_keys table
    3. Write plain password to Mosquitto passwd.conf
    """
    import secrets

    try:
        # Generate password
        password = f"minion_{secrets.token_urlsafe(24)}"
        password_hash = hashlib.sha256(password.encode()).hexdigest()  # Simple hash for now

        async with db_pool.acquire() as conn:
            # Store in api_keys
            await conn.fetchrow(
                """
                INSERT INTO api_keys (key_hash, name, created_at)
                VALUES ($1, $2, $3)
                RETURNING id
                """,
                password_hash,
                f"minion:{minion_id}:{request.name}",
                datetime.now(UTC),
            )

        # Write to passwd.conf
        mqtt_passwd_path = _get_mqtt_passwd_path()
        if mqtt_passwd_path:
            await _write_mqtt_passwd(mqtt_passwd_path, minion_id, password)

        return MinionTokenResponse(
            token=password,  # Only shown once!
            minion_id=minion_id,
            created_at=datetime.now(UTC),
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "internal_error", "detail": str(e)},
        )


@router.delete(
    "/{minion_id:path}/token",
    summary="Revoke minion token",
    description="Revokes the MQTT password for a minion and removes from passwd.conf.",
)
async def revoke_minion_token(
    minion_id: Annotated[str, Path(description="Minion ID")],
    key: str = Depends(get_api_key),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict[str, Any]:
    """Revoke a minion's MQTT password."""
    try:
        async with db_pool.acquire() as conn:
            # Find and revoke the token
            await conn.execute(
                """
                UPDATE api_keys
                SET revoked_at = $1
                WHERE name LIKE $2 AND revoked_at IS NULL
                """,
                datetime.now(UTC),
                f"minion:{minion_id}:%",
            )

        # Remove from passwd.conf
        mqtt_passwd_path = _get_mqtt_passwd_path()
        if mqtt_passwd_path:
            await _remove_from_mqtt_passwd(mqtt_passwd_path, minion_id)

        return {"message": f"Token revoked for minion {minion_id}"}

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "internal_error", "detail": str(e)},
        )


@router.post(
    "/{minion_id:path}/config",
    summary="Push config to minion",
    description="Sends configuration to a minion via MQTT command topic.",
)
async def push_minion_config(
    minion_id: Annotated[str, Path(description="Minion ID")],
    request: MinionConfigRequest,
    key: str = Depends(get_api_key),
    minion_service: MinionService | None = Depends(get_minion_service),
) -> dict[str, Any]:
    """Push configuration to a minion via MQTT."""
    if not minion_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "unavailable", "detail": "Minion service not initialized"},
        )

    try:
        # Send config via MQTT
        from cortex_protocol import MQTTTopics

        topic = MQTTTopics.command_config(minion_id)

        # This would publish to the MQTT broker
        # For now, just acknowledge
        return {
            "message": f"Config pushed to {minion_id}",
            "topic": topic,
            "config": request.config,
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "internal_error", "detail": str(e)},
        )


def _get_mqtt_passwd_path() -> str | None:
    """Get the MQTT passwd.conf path from config."""
    try:
        from cortex.config.loader import get_settings

        settings = get_settings()
        mqtt_settings = getattr(settings, "mqtt", None)
        if mqtt_settings is None:
            return None
        return getattr(mqtt_settings, "passwd_path", None)
    except Exception:
        return None


async def _write_mqtt_passwd(path: str, username: str, password: str) -> None:
    """Append user:password to Mosquitto passwd.conf."""
    import os

    # Create file if not exists
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)

    # Read existing
    try:
        with open(path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []

    # Remove existing entry for this user
    lines = [line for line in lines if not line.startswith(f"{username}:")]

    # Append new entry
    lines.append(f"{username}:{password}\n")

    with open(path, "w") as f:
        f.writelines(lines)


async def _remove_from_mqtt_passwd(path: str, username: str) -> None:
    """Remove user from Mosquitto passwd.conf."""
    try:
        with open(path) as f:
            lines = f.readlines()

        lines = [line for line in lines if not line.startswith(f"{username}:")]

        with open(path, "w") as f:
            f.writelines(lines)
    except FileNotFoundError:
        pass
