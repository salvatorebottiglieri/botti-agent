"""Safety-net settings slice (ADR-0021, issues #146/#147).

The safety store holds backups, file snapshots and the journal. It lives at
``~/.local/share/cortex`` — outside the repo and outside every Docker volume —
because the incident this net exists for was a volume wipe.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import SettingsConfigDict

from cortex.config.base import CortexSettings


class SafetySettings(CortexSettings):
    """Backup/snapshot/journal store and pre-migration policy (env ``SAFETY_``)."""

    model_config = SettingsConfigDict(env_prefix="SAFETY_")

    store_dir: Path = Field(
        default_factory=lambda: Path.home() / ".local" / "share" / "cortex",
        description="Root for backups, snapshots and the journal; outside the "
        "repo and every Docker volume",
    )
    backup_retention: int = Field(
        default=14,
        ge=0,
        description="Newest N backups kept; 0 disables pruning",
    )
    backup_on_migration: bool = Field(
        default=True,
        description="Take a dump before applying pending migrations",
    )
    require_backup_before_migration: bool = Field(
        default=True,
        description="Abort when the pre-migration dump fails",
    )
    snapshot_enabled: bool = Field(
        default=True,
        description="Snapshot existing files before file_write overwrites them",
    )

    @field_validator("store_dir", mode="before")
    @classmethod
    def _expand_user(cls, value: object) -> object:
        """Expand ``~`` in an env/YAML value before ``Path`` validation."""
        if isinstance(value, str):
            return Path(value).expanduser()
        if isinstance(value, Path):
            return value.expanduser()
        return value
