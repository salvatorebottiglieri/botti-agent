"""Shared base for the Cortex settings slices."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class CortexSettings(BaseSettings):
    """Base for every settings slice composed by :class:`cortex.config.models.Settings`.

    Each slice loads ``.env`` itself: a nested ``BaseSettings`` reads its own
    sources, and ``_env_file=None`` passed to the root does **not** propagate to
    it. A slice that needs its own environment namespace overrides
    ``env_prefix`` in its ``model_config`` (pydantic merges the subclass config
    over this one).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
