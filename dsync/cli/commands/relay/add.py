"""CLI command to add a new relay server."""

from __future__ import annotations

from typing import Annotated

import typer

from dsync.cli.console import error, success
from dsync.config import RelayServer
from dsync.crypto import is_valid_fingerprint
from dsync.state import AppState


def add(
    ctx: typer.Context,
    id: Annotated[str, typer.Argument(help="Unique relay id")],
    host: Annotated[str, typer.Argument(help="Relay hostname or IP address")],
    port: Annotated[int, typer.Argument(help="Relay UDP port (1-65535)")],
    fingerprint: Annotated[str, typer.Argument(help="Relay public-key fingerprint")],
) -> None:
    """Add a new relay server and persist it to relays.yaml."""
    state: AppState = ctx.obj

    if any(entry.id == id for entry in state.relays.relays):
        error(f"Relay with id '{id}' already exists")
        raise typer.Exit(code=1)

    if any(entry.fingerprint == fingerprint for entry in state.relays.relays):
        error(f"Relay with fingerprint '{fingerprint}' already exists")
        raise typer.Exit(code=1)

    if not is_valid_fingerprint(fingerprint):
        error("Fingerprint does not match expected format")
        raise typer.Exit(code=1)

    if not 1 <= port <= 65535:
        error(f"Port {port} is out of range (1-65535)")
        raise typer.Exit(code=1)

    state.relays.relays.append(
        RelayServer(
            id=id,
            host=host,
            port=port,
            fingerprint=fingerprint,
        )
    )

    state.relays.save(state.config_dir, overwrite=True)
    lines = [
        "Added relay:",
        f"    • {id}",
        f"      address:     {host}:{port}",
        f"      fingerprint: {fingerprint}",
    ]

    success("\n".join(lines))
