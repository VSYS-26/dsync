"""App-start callback: resolve config directory and load both configs."""

from pathlib import Path
from typing import Annotated

import typer

from dsync.cli.console import info, warn
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
    if not directory.is_dir():
        warn(f"config directory {directory} does not exist, starting empty")
    else:
        for filename in (FoldersConfig.FILENAME, DevicesConfig.FILENAME):
            if not (directory / filename).is_file():
                warn(f"{directory / filename} does not exist, starting empty")

    folders = FoldersConfig.load(directory)
    devices = DevicesConfig.load(directory)

    info(
        f"loaded config from {directory}: "
        f"{len(folders.entries)} folders, {len(devices.trusted_devices)} devices"
    )

    ctx.obj = AppState(config_dir=directory, folders=folders, devices=devices)
