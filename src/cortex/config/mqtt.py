"""MQTT settings slice."""

from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import SettingsConfigDict

from cortex.config.base import CortexSettings


class MQTTSettings(CortexSettings):
    """Broker settings for the minion gateway (env namespace ``MQTT_``)."""

    model_config = SettingsConfigDict(env_prefix="MQTT_")

    broker_url: str = Field(default="mqtt://localhost:1883", description="MQTT broker URL")
    username: str | None = Field(default=None)
    password: SecretStr | None = Field(default=None)
    client_id_prefix: str = Field(default="cortex")
    keepalive: int = Field(default=60, ge=1)
    reconnect_interval: int = Field(default=5, ge=1)
