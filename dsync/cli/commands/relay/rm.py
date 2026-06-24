"""CLI command to remove a relay server."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import typer

from dsync.cli.console import error, success, warn

if TYPE_CHECKING:
    from dsync.state import AppState


def rm(
    ctx: typer.Context,
    id: Annotated[str, typer.Argument(help="Unique relay id")],
) -> None:
    """Remove a relay server from relays.yaml.

    Refuses to remove a relay that is still referenced by a trusted device.
    """
    state: AppState = ctx.obj

    entry = next((r for r in state.relays.relays if r.id == id), None)
    if entry is None:
        warn(f"The specified relay '{id}' is not configured.")
        return

    referencing = [d.id for d in state.devices.trusted_devices if d.relay_id == id]
    if referencing:
        error(
            f"Relay '{id}' is still referenced by device(s): "
            f"{', '.join(referencing)}. Update those devices first."
        )
        raise typer.Exit(code=1)

    state.relays.relays.remove(entry)
    state.relays.save(state.config_dir, overwrite=True)
    lines = [
        "Removed relay:",
        f"    • {entry.id}",
        f"      address:     {entry.host}:{entry.port}",
        f"      fingerprint: {entry.fingerprint}",
    ]

    success("\n".join(lines))
