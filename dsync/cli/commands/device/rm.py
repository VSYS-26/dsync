"""CLI command to remove a trusted device."""

from __future__ import annotations

from typing import Annotated

import typer

from dsync.cli.console import success, warn
from dsync.state import AppState


def rm(
    ctx: typer.Context,
    id: Annotated[str, typer.Argument(help="Unique device id")],
) -> None:
    """Remove a trusted device."""
    state: AppState = ctx.obj

    entry = next((d for d in state.devices.trusted_devices if d.id == id), None)
    if entry is None:
        warn(f"The specified device '{id}' is not configured.")
        return

    state.devices.trusted_devices.remove(entry)
    state.devices.save(state.config_dir, overwrite=True)
    lines = [
        "Removed device:",
        f"    • {entry.id}",
        f"      fingerprint: {entry.fingerprint}",
        f"      relay_id:    {entry.relay_id}",
    ]

    success("\n".join(lines))
