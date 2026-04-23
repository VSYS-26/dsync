"""Folder sync configuration models and CRUD helpers."""

from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dsync.config._base import YamlFileConfig


class SyncMode(StrEnum):
    """Sync direction for a folder entry."""

    MIRROR = "mirror"
    BACKUP_TO_PEER = "backup-to-peer"


class FolderEntry(BaseModel):
    """A single folder or file configured for synchronization."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    path: Path
    mode: SyncMode


class FoldersConfig(YamlFileConfig):
    """Aggregate of all configured folder entries."""

    model_config = ConfigDict(extra="forbid")
    FILENAME: ClassVar[str] = "folders.yaml"
    entries: list[FolderEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> "FoldersConfig":
        """Ensure entry ids are unique."""
        ids = [e.id for e in self.entries]
        if len(set(ids)) != len(ids):
            raise ValueError("entries: duplicate id")
        return self
