"""CLI command to modify a folder configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from dsync.cli.console import error, success
from dsync.config import SyncMode
from dsync.state import AppState


def mod(
    ctx: typer.Context,
    id: Annotated[str, typer.Argument(help="Unique folder id")],
    path: Annotated[
        Path | None,
        typer.Option("--path", "-p", help="Folder path")
    ] = None,
    mode: Annotated[
        SyncMode | None,
        typer.Option("--mode", help="Sync mode")
    ] = None,
    devices: Annotated[
        list[str] | None,
        typer.Option(
            "--device",
            "-d",
            help="Trusted device ids (repeatable). If omitted, the field stays unchanged.",
        ),
    ] = None,
) -> None:
    """Modify an existing folder and persist it to folders.yaml."""
    state: AppState = ctx.obj

    if path is None and mode is None and devices is None:
        error("At least one of --path, --mode, or --device must be provided")
        raise typer.Exit(code=1)

    current = next((entry for entry in state.folders.entries if entry.id == id), None)
    if current is None:
        error(f"Folder with id '{id}' does not exist")
        raise typer.Exit(code=1)

    trusted_ids = {device.id for device in state.devices.trusted_devices}

    if devices is not None:
        unknown = [device_id for device_id in devices if device_id not in trusted_ids]
        if unknown:
            error(f"Unknown trusted device id(s): {', '.join(unknown)}")
            raise typer.Exit(code=1)

    updated = current.model_copy(
        update={
            **({"path": path} if path is not None else {}),
            **({"mode": mode} if mode is not None else {}),
            **({"devices": devices} if devices is not None else {}),
        }
    )

    state.folders.entries = [updated if entry.id == id else entry for entry in state.folders.entries]

    state.folders.save(state.config_dir, overwrite=True)
    lines = [
        "Modified folder:",
        f"    • {updated.id}",
        f"      path:    {updated.path}",
        f"      mode:    {updated.mode.value}",
    ]

    if updated.devices is not None:
        lines.append(f"      devices: {', '.join(updated.devices)}")

    success("\n".join(lines))
