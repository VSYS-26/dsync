"""CLI command to modify a trusted device."""

from __future__ import annotations

from typing import Annotated

import typer

from dsync.cli.console import error, success
from dsync.crypto import is_valid_fingerprint
from dsync.state import AppState


def mod(
    ctx: typer.Context,
    id: Annotated[str, typer.Argument(help="Unique device id")],
    fingerprint: Annotated[
        str | None,
        typer.Option("--fingerprint", "-f", help="New device fingerprint"),
    ] = None,
    relay_id: Annotated[
        str | None,
        typer.Option("--relay-id", "-r", help="New relay id (must exist in relays.yaml)"),
    ] = None,
) -> None:
    """Update a trusted device and persist it to devices.yaml."""
    state: AppState = ctx.obj

    if fingerprint is None and relay_id is None:
        error("At least one of --fingerprint or --relay-id must be provided")
        raise typer.Exit(code=1)

    current = next((entry for entry in state.devices.trusted_devices if entry.id == id), None)
    if current is None:
        error(f"Device with id '{id}' does not exist")
        raise typer.Exit(code=1)

    if fingerprint is not None:
        if any(
            entry.fingerprint == fingerprint and entry.id != id
            for entry in state.devices.trusted_devices
        ):
            error(f"Device with fingerprint '{fingerprint}' already exists")
            raise typer.Exit(code=1)
        if not is_valid_fingerprint(fingerprint):
            error("Fingerprint does not match expected format")
            raise typer.Exit(code=1)

    if relay_id is not None and not any(r.id == relay_id for r in state.relays.relays):
        error(f"Relay '{relay_id}' is not listed in relays.yaml")
        raise typer.Exit(code=1)

    updated = current.model_copy(
        update={
            **({"fingerprint": fingerprint} if fingerprint is not None else {}),
            **({"relay_id": relay_id} if relay_id is not None else {}),
        }
    )

    state.devices.trusted_devices = [
        updated if entry.id == id else entry for entry in state.devices.trusted_devices
    ]

    state.devices.save(state.config_dir, overwrite=True)
    lines = [
        "Modified device:",
        f"    • {updated.id}",
        f"      fingerprint: {updated.fingerprint}",
        f"      relay_id:    {updated.relay_id}",
    ]

    success("\n".join(lines))
