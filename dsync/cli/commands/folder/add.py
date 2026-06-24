"""CLI command to add a new folder."""

from __future__ import annotations

# Path is needed at runtime: typer resolves the annotation at registration.
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Annotated

from pydantic import ValidationError
import typer

from dsync.cli.console import error, success
from dsync.cli.daemon_ops import refresh_server_daemon
from dsync.config import FolderEntry, SyncMode

if TYPE_CHECKING:
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
    interval: Annotated[
        str | None,
        typer.Option(
            "--interval",
            help="Cron expression for automatic sync, e.g. '*/30 * * * *'. Omit for manual only.",
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

    try:
        entry = FolderEntry(id=id, path=path, mode=mode, devices=devices, interval=interval)
    except ValidationError as exc:
        error(f"Invalid folder configuration: {exc}")
        raise typer.Exit(code=1) from exc

    state.folders.entries.append(entry)
    state.folders.save(state.config_dir, overwrite=True)
    refresh_server_daemon(state)

    lines = [
        "Added folder:",
        f"    • {entry.id}",
        f"      path:    {entry.path}",
        f"      mode:    {entry.mode.value}",
    ]
    if entry.devices is not None:
        lines.append(f"      devices: {', '.join(entry.devices)}")
    if entry.interval is not None:
        lines.append(f"      interval: {entry.interval}")

    success("\n".join(lines))
