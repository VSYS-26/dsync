"""CLI command to modify a trusted device."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from dsync.cli.console import error, success
from dsync.config import SyncMode
from dsync.state import AppState
from dsync.crypto import is_valid_fingerprint


def mod(
    ctx: typer.Context,
    id: Annotated[str, typer.Argument(help="Unique device id")],
    fingerprint: Annotated[str, typer.Argument(help="New device fingerprint")],
) -> None:
    """Update fingerprint of trusted device and persist it to devices.yaml."""
    state: AppState = ctx.obj

    current = next((entry for entry in state.devices.trusted_devices if entry.id == id), None)
    if current is None:
        error(f"Device with id '{id}' does not exist")
        raise typer.Exit(code=1)

    if any(entry.fingerprint == fingerprint and entry.id != id for entry in state.devices.trusted_devices):
        error(f"Device with fingerprint '{fingerprint}' already exists")
        raise typer.Exit(code=1)

    if not is_valid_fingerprint(fingerprint):
        error(f"Fingerprint does not match expected format")
        raise typer.Exit(code=1)

    updated = current.model_copy(
        update={
            **({"fingerprint": fingerprint}),
        }
    )

    state.devices.trusted_devices = [updated if entry.id == id else entry for entry in state.devices.trusted_devices]

    state.devices.save(state.config_dir, overwrite=True)
    lines = [
        "Modified device fingerprint:",
        f"    • {updated.id}",
        f"      fingerprint:    {updated.fingerprint}",
    ]

    success("\n".join(lines))
