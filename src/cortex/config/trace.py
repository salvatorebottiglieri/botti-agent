"""Loop-trace settings slice."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from cortex.config.base import CortexSettings


class TraceSettings(CortexSettings):
    """Loop-trace capture settings (env namespace ``TRACE_``).

    Not in the original #35 slice list — the trace fields it carries arrived
    with issues #112/#114, after that ticket was written.
    """

    model_config = SettingsConfigDict(env_prefix="TRACE_")

    sidecar_url: str = Field(
        default="http://127.0.0.1:5005",
        description="Base URL of the rizzo-pii pseudonymization sidecar",
    )
    sidecar_timeout_s: float = Field(
        default=10.0,
        gt=0,
        description="Per-request timeout in seconds for sidecar /analyze calls",
    )
    # Loop-event rows older than this many days are eligible for deletion by
    # `cortex traces:cleanup` (issue #114 T4).
    retention_days: int = Field(
        default=30,
        ge=1,
        description="Days of loop_events history to keep; older rows are "
        "deleted by `cortex traces:cleanup`",
    )
