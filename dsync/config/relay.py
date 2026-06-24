"""Relay-servers configuration models and CRUD helpers."""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dsync.config._base import YamlFileConfig


class RelayServer(BaseModel):
    """A relay server used as a pure rendezvous point for NAT hole-punching."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    fingerprint: str = Field(min_length=1)


class RelaysConfig(YamlFileConfig):
    """Aggregate of all configured relay-server entries."""

    model_config = ConfigDict(extra="forbid")
    FILENAME: ClassVar[str] = "relays.yaml"
    relays: list[RelayServer] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> "RelaysConfig":
        """Ensure both relay ids and fingerprints are unique."""
        ids = [r.id for r in self.relays]
        fps = [r.fingerprint for r in self.relays]
        if len(set(ids)) != len(ids):
            raise ValueError("relays: duplicate id")
        if len(set(fps)) != len(fps):
            raise ValueError("relays: duplicate fingerprint")
        return self
