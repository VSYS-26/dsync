"""CLI command to add a new device."""

from __future__ import annotations

from typing import Annotated

import typer

from dsync.cli.console import error, success
from dsync.config import TrustedDevice
from dsync.crypto import is_valid_fingerprint
from dsync.state import AppState


def add(
    ctx: typer.Context,
    id: Annotated[str, typer.Argument(help="Unique device id")],
    fingerprint: Annotated[str, typer.Argument(help="Device fingerprint")],
    relay_id: Annotated[
        str,
        typer.Argument(help="Relay id (from relays.yaml) the device is reachable through"),
    ],
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
        error("Fingerprint does not match expected format")
        raise typer.Exit(code=1)

    if not any(r.id == relay_id for r in state.relays.relays):
        error(f"Relay '{relay_id}' is not listed in relays.yaml")
        raise typer.Exit(code=1)

    state.devices.trusted_devices.append(
        TrustedDevice(
            id=id,
            fingerprint=fingerprint,
            relay_id=relay_id,
        )
    )

    state.devices.save(state.config_dir, overwrite=True)
    e = next(d for d in state.devices.trusted_devices if d.id == id)
    lines = [
        "Added device:",
        f"    • {e.id}",
        f"      fingerprint: {e.fingerprint}",
        f"      relay_id:    {e.relay_id}",
    ]

    success("\n".join(lines))
