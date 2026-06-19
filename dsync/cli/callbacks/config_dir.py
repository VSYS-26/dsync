"""App-start callback: resolve config directory and load configs into AppState."""

from pathlib import Path
from typing import Annotated

import typer

from dsync.cli.console import error, info, warn
from dsync.config import DevicesConfig, FoldersConfig
from dsync.state import AppState

DEFAULT_CONFIG_DIR = Path("./dsync-config")


def config_dir(
    ctx: typer.Context,
    directory: Annotated[
        Path,
        typer.Option(
            "--config-dir",
            "-c",
            help="Directory containing folders.yaml and devices.yaml.",
        ),
    ] = DEFAULT_CONFIG_DIR,
) -> None:
    """Load folder and device configs into the Typer AppState context."""
    if not directory.exists():
        warn(f"config directory {directory} does not exist, starting empty")
    elif not directory.is_dir():
        raise typer.BadParameter(f"{directory} exists but is not a directory")
    else:
        for filename in (FoldersConfig.FILENAME, DevicesConfig.FILENAME):
            if not (directory / filename).is_file():
                warn(f"{directory / filename} does not exist, starting empty")

    try:
        state = AppState.load(directory)
    except ValueError as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc

    info(
        f"loaded config from {directory}: "
        f"{len(state.folders.entries)} folders, {len(state.devices.trusted_devices)} devices"
    )

    ctx.obj = state
