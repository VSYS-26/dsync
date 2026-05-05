"""Trusted-devices configuration models and CRUD helpers."""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dsync.config._base import YamlFileConfig


class TrustedDevice(BaseModel):
    """A trusted peer device identified by its public-key fingerprint."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    fingerprint: str = Field(min_length=1)


class DevicesConfig(YamlFileConfig):
    """Aggregate of all trusted device entries."""

    model_config = ConfigDict(extra="forbid")
    FILENAME: ClassVar[str] = "devices.yaml"
    trusted_devices: list[TrustedDevice] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> "DevicesConfig":
        """Ensure both device ids and fingerprints are unique."""
        ids = [d.id for d in self.trusted_devices]
        fps = [d.fingerprint for d in self.trusted_devices]
        if len(set(ids)) != len(ids):
            raise ValueError("trusted_devices: duplicate id")
        if len(set(fps)) != len(fps):
            raise ValueError("trusted_devices: duplicate fingerprint")
        return self
