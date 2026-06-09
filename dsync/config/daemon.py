"""Daemon configuration models and helpers."""

from typing import ClassVar

from pydantic import ConfigDict, Field

from dsync.config._base import YamlFileConfig


class DaemonConfig(YamlFileConfig):
    """Configuration for background daemon mode (server only)."""

    model_config = ConfigDict(extra="forbid")
    FILENAME: ClassVar[str] = "daemon.yaml"

    enabled: bool = Field(default=False, description="Whether daemon mode is enabled")
    port: int = Field(default=9999, ge=1, le=65535, description="Network port for daemon")
    cert: str = Field(default="cert.pem", description="Path to TLS certificate")
    key: str = Field(default="key.pem", description="Path to TLS private key")
