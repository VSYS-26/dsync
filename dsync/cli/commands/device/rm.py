"""CLI remove a trusted device."""

from __future__ import annotations

from typing import Annotated

import typer

from dsync.cli.console import error, success
from dsync.state import AppState


def rm(ctx: typer.Context, id: Annotated[str, typer.Argument(help="Unique device id")]) -> None:
    """Remove a trusted device."""
    state: AppState = ctx.obj

    filtered = list(filter(lambda f: f.id == id, state.devices.trusted_devices))
    e = filtered[0] if filtered else None

    if not e:
        error(f"The specified device {id} is not configured.")
        return

    state.devices.trusted_devices.remove(e)

    state.devices.save(state.config_dir, overwrite=True)
    lines = [
        "Removed device:",
        f"    • {e.id}",
        f"      fingerprint:    {e.fingerprint}",
    ]

    success("\n".join(lines))
