"""Meta-tool allow-list settings slice (issue #134)."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import NoDecode, SettingsConfigDict

from cortex.config.base import CortexSettings

#: Tools registered when ``TOOLS_ALLOW`` is unset: read-only, plus ``ask_user``.
#: ``shell`` and ``file_write`` are opt-in until #120 owns path/argument policy
#: and per-call approval.
DEFAULT_ALLOWED_TOOLS: tuple[str, ...] = ("file_read", "grep", "ask_user")


class ToolsSettings(CortexSettings):
    """Meta tools the composition root registers (env namespace ``TOOLS_``).

    Deny-by-default; paths, arguments and per-call approval are #120.

    ``allow`` is ``NoDecode`` with a before-validator because pydantic-settings
    JSON-decodes a list field read from the environment: ``TOOLS_ALLOW=file_read,grep``
    and an explicit ``TOOLS_ALLOW=""`` would raise instead of parsing. The names
    are not validated here — ``register_meta_tools`` raises on an unknown one, so
    a typo has a single loud failure point at startup.
    """

    model_config = SettingsConfigDict(env_prefix="TOOLS_")

    allow: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(DEFAULT_ALLOWED_TOOLS)
    )

    @field_validator("allow", mode="before")
    @classmethod
    def _decode_allow(cls, value: object) -> object:
        """Accept a CSV string or an already-parsed list.

        The result is left to pydantic's own ``list[str]`` validation.
        """
        if not isinstance(value, str):
            return value

        text = value.strip()
        if not text:
            return []
        return [name.strip() for name in text.split(",") if name.strip()]
