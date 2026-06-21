"""Scheduler daemon configuration model."""

from typing import ClassVar

from pydantic import ConfigDict, Field

from dsync.config._base import YamlFileConfig


class SchedulerConfig(YamlFileConfig):
    """Persisted enabled state and TLS paths for the scheduler daemon.

    Lives next to ``daemon.yaml``; both daemons can be enabled independently.
    """

    model_config = ConfigDict(extra="forbid")
    FILENAME: ClassVar[str] = "scheduler.yaml"

    enabled: bool = Field(default=False, description="Whether the scheduler daemon is enabled")
    cert: str = Field(default="cert.pem", description="Path to TLS certificate")
    key: str = Field(default="key.pem", description="Path to TLS private key")
