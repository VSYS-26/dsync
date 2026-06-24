"""CLI command to modify a folder configuration."""

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


def mod(
    ctx: typer.Context,
    id: Annotated[str, typer.Argument(help="Unique folder id")],
    path: Annotated[Path | None, typer.Option("--path", "-p", help="Folder path")] = None,
    mode: Annotated[SyncMode | None, typer.Option("--mode", help="Sync mode")] = None,
    devices: Annotated[
        list[str] | None,
        typer.Option(
            "--device",
            "-d",
            help="Trusted device ids (repeatable). If omitted, the field stays unchanged.",
        ),
    ] = None,
    interval: Annotated[
        str | None,
        typer.Option("--interval", help="Cron expression for automatic sync, e.g. '*/30 * * * *'"),
    ] = None,
    clear_interval: Annotated[
        bool,
        typer.Option("--clear-interval", help="Remove the automatic sync schedule"),
    ] = False,
) -> None:
    """Modify an existing folder and persist it to folders.yaml."""
    state: AppState = ctx.obj

    if (
        path is None
        and mode is None
        and devices is None
        and interval is None
        and not clear_interval
    ):
        error(
            "At least one of --path, --mode, --device, --interval, "
            "or --clear-interval must be provided"
        )
        raise typer.Exit(code=1)

    if interval is not None and clear_interval:
        error("--interval and --clear-interval are mutually exclusive")
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

    data = current.model_dump()
    data.update(
        {
            **({"path": path} if path is not None else {}),
            **({"mode": mode} if mode is not None else {}),
            **({"devices": devices} if devices is not None else {}),
            **({"interval": interval} if interval is not None else {}),
            **({"interval": None} if clear_interval else {}),
        }
    )

    try:
        updated = FolderEntry.model_validate(data)
    except ValidationError as exc:
        error(f"Invalid folder configuration: {exc}")
        raise typer.Exit(code=1) from exc

    state.folders.entries = [
        updated if entry.id == id else entry for entry in state.folders.entries
    ]
    state.folders.save(state.config_dir, overwrite=True)
    refresh_server_daemon(state)

    lines = [
        "Modified folder:",
        f"    • {updated.id}",
        f"      path:    {updated.path}",
        f"      mode:    {updated.mode.value}",
    ]
    if updated.devices is not None:
        lines.append(f"      devices: {', '.join(updated.devices)}")
    if updated.interval is not None:
        lines.append(f"      interval: {updated.interval}")

    success("\n".join(lines))
