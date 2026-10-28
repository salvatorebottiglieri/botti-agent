"""Meta-tool allow-list settings slice (issue #134)."""

from __future__ import annotations

import json
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

    Deny-by-default: only the names in ``allow`` reach the registry, so a
    prompt-injected turn cannot run ``shell`` or write files unless the operator
    opted in. ``ask_user`` is in the default set because it touches no host
    resource — it only ends the turn with a question. The real policy (paths,
    arguments, per-call approval) is #120; this slice is the phase-0 mitigation.

    ``allow`` is ``NoDecode`` with a before-validator because pydantic-settings
    JSON-decodes a list field read from the environment: the documented
    ``TOOLS_ALLOW=file_read,grep`` and an explicit ``TOOLS_ALLOW=""`` would
    raise instead of parsing. The names are not validated here —
    ``register_meta_tools`` raises on an unknown one, so a typo has exactly one
    loud failure point at startup.
    """

    model_config = SettingsConfigDict(env_prefix="TOOLS_")

    allow: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(DEFAULT_ALLOWED_TOOLS)
    )

    @field_validator("allow", mode="before")
    @classmethod
    def _decode_allow(cls, value: object) -> object:
        """Accept a JSON list, a CSV string, or an already-parsed list.

        The result is left to pydantic's own ``list[str]`` validation.
        """
        if not isinstance(value, str):
            return value

        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            return json.loads(text)
        return [name.strip() for name in text.split(",") if name.strip()]
