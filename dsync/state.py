"""Runtime state shared between CLI commands."""

from dataclasses import dataclass
from pathlib import Path

from dsync.config import DevicesConfig, FoldersConfig


@dataclass(frozen=True)
class AppState:
    """Application state for the running dsync process.

    Access from a CLI command via ``typer.Context.obj``::

        def my_command(ctx: typer.Context) -> None:
            state: AppState = ctx.obj
    """

    config_dir: Path
    folders: FoldersConfig
    devices: DevicesConfig
