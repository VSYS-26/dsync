"""Base mixin for Pydantic models backed by a YAML file."""

from pathlib import Path
from typing import ClassVar, Self

from pydantic import BaseModel
import yaml


class YamlFileConfig(BaseModel):
    """Base for Pydantic models backed by a YAML file in a directory."""

    FILENAME: ClassVar[str]

    @classmethod
    def load(cls, directory: Path) -> Self:
        """Load the config from ``directory / cls.FILENAME`` (empty model if absent)."""
        file = directory / cls.FILENAME
        if not file.is_file():
            return cls()
        return cls.model_validate(yaml.safe_load(file.read_text(encoding="utf-8")) or {})

    def save(self, directory: Path, *, overwrite: bool = False) -> None:
        """Write the config to ``directory / FILENAME`` (raise FileExistsError if present)."""
        file = directory / self.FILENAME
        if file.exists() and not overwrite:
            raise FileExistsError(file)
        directory.mkdir(parents=True, exist_ok=True)
        file.write_text(
            yaml.safe_dump(self.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
