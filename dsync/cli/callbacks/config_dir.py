"""App-start callback: resolve config directory and load both configs."""

from pathlib import Path
from typing import Annotated

import typer

from dsync.cli.console import error, info, warn
from dsync.config import DaemonConfig, DevicesConfig, FoldersConfig
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

    folders = FoldersConfig.load(directory)
    devices = DevicesConfig.load(directory)
    daemon = DaemonConfig.load(directory)

    # If devices field is omitted, use all trusted devices
    resolved_entries = []
    trusted_device_ids = [dev.id for dev in devices.trusted_devices]
    for entry in folders.entries:
        if entry.devices is None:
            resolved_entries.append(entry.model_copy(update={"devices": trusted_device_ids}))
        else:
            for device_id in entry.devices:
                if device_id not in trusted_device_ids:
                    error(
                        f"Trusted device {device_id} of folder {directory} is not listed in devices.yaml"
                    )
                    raise ValueError(
                        f"Trusted device {device_id} of folder {directory} is not listed in devices.yaml"
                    )
            resolved_entries.append(entry.model_copy(update={"devices": entry.devices}))
    folders.entries = resolved_entries

    info(
        f"loaded config from {directory}: "
        f"{len(folders.entries)} folders, {len(devices.trusted_devices)} devices"
    )

    ctx.obj = AppState(config_dir=directory, folders=folders, devices=devices, daemon=daemon)
