"""CLI command to add a new device."""

from __future__ import annotations

from typing import Annotated

import typer

from dsync.cli.console import error, success
from dsync.config import TrustedDevice
from dsync.state import AppState
from dsync.crypto import is_valid_fingerprint


def add(
    ctx: typer.Context,
    id: Annotated[str, typer.Argument(help="Unique device id")],
    fingerprint: Annotated[str, typer.Argument(help="Device fingerprint")],
) -> None:
    """Add a new device and persist it to devices.yaml."""
    state: AppState = ctx.obj

    if any(entry.id == id for entry in state.devices.trusted_devices):
        error(f"Device with id '{id}' already exists")
        raise typer.Exit(code=1)

    if any(entry.fingerprint == fingerprint for entry in state.devices.trusted_devices):
        error(f"Device with fingerprint '{fingerprint}' already exists")
        raise typer.Exit(code=1)

    if not is_valid_fingerprint(fingerprint):
        error(f"Fingerprint does not match expected format")
        raise typer.Exit(code=1)

    state.devices.trusted_devices.append(
        TrustedDevice(
            id=id,
            fingerprint=fingerprint
        )
    )

    state.devices.save(state.config_dir, overwrite=True)
    e = list(filter(lambda f: f.id == id, state.devices.trusted_devices))[0]
    lines = [
        "Added device:",
        f"    • {e.id}",
        f"      fingerprint:    {e.fingerprint}",
    ]

    success("\n".join(lines))
