"""CLI command to modify a relay-server configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import typer

from dsync.cli.console import error, success
from dsync.crypto import is_valid_fingerprint

if TYPE_CHECKING:
    from dsync.state import AppState


def mod(
    ctx: typer.Context,
    id: Annotated[str, typer.Argument(help="Unique relay id")],
    host: Annotated[
        str | None,
        typer.Option("--host", "-H", help="Relay hostname or IP address"),
    ] = None,
    port: Annotated[
        int | None,
        typer.Option("--port", "-p", help="Relay UDP port (1-65535)"),
    ] = None,
    fingerprint: Annotated[
        str | None,
        typer.Option("--fingerprint", "-f", help="Relay public-key fingerprint"),
    ] = None,
) -> None:
    """Modify an existing relay server and persist it to relays.yaml."""
    state: AppState = ctx.obj

    if host is None and port is None and fingerprint is None:
        error("At least one of --host, --port, or --fingerprint must be provided")
        raise typer.Exit(code=1)

    current = next((entry for entry in state.relays.relays if entry.id == id), None)
    if current is None:
        error(f"Relay with id '{id}' does not exist")
        raise typer.Exit(code=1)

    if fingerprint is not None:
        if any(
            entry.fingerprint == fingerprint and entry.id != id for entry in state.relays.relays
        ):
            error(f"Relay with fingerprint '{fingerprint}' already exists")
            raise typer.Exit(code=1)
        if not is_valid_fingerprint(fingerprint):
            error("Fingerprint does not match expected format")
            raise typer.Exit(code=1)

    if port is not None and not 1 <= port <= 65535:
        error(f"Port {port} is out of range (1-65535)")
        raise typer.Exit(code=1)

    updated = current.model_copy(
        update={
            **({"host": host} if host is not None else {}),
            **({"port": port} if port is not None else {}),
            **({"fingerprint": fingerprint} if fingerprint is not None else {}),
        }
    )

    state.relays.relays = [updated if entry.id == id else entry for entry in state.relays.relays]

    state.relays.save(state.config_dir, overwrite=True)
    lines = [
        "Modified relay:",
        f"    • {updated.id}",
        f"      address:     {updated.host}:{updated.port}",
        f"      fingerprint: {updated.fingerprint}",
    ]

    success("\n".join(lines))
