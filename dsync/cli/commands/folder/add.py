"""CLI command to add a new folder."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from dsync.cli.console import error, success
from dsync.config import FolderEntry, SyncMode
from dsync.state import AppState


def add(
    ctx: typer.Context,
    id: Annotated[str, typer.Argument(help="Unique folder id")],
    path: Annotated[Path, typer.Argument(help="Folder path")],
    mode: Annotated[SyncMode, typer.Option(help="Sync mode")],
    devices: Annotated[
        list[str] | None,
        typer.Option(
            "--device",
            "-d",
            help="Trusted device ids (repeatable). If omitted, the field stays absent in YAML.",
        ),
    ] = None,
) -> None:
    """Add a new folder and persist it to folders.yaml."""
    state: AppState = ctx.obj

    if any(entry.id == id for entry in state.folders.entries):
        error(f"Folder with id '{id}' already exists")
        raise typer.Exit(code=1)

    trusted_ids = {device.id for device in state.devices.trusted_devices}

    if devices is not None:
        unknown = [device_id for device_id in devices if device_id not in trusted_ids]
        if unknown:
            error(f"Unknown trusted device id(s): {', '.join(unknown)}")
            raise typer.Exit(code=1)

    state.folders.entries.append(
        FolderEntry(
            id=id,
            path=path,
            mode=mode,
            devices=devices,
        )
    )

    state.folders.save(state.config_dir, overwrite=True)
    e = list(filter(lambda f: f.id == id, state.folders.entries))[0]
    lines = ["Added folder:", f"""\
    • {e.id}
      path:    {e.path}
      mode:    {e.mode.value}
      devices: {", ".join(e.devices)}
    """]

    success("\n".join(lines))
