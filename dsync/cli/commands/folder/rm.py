"""CLI remove an folder configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from dsync.cli.console import error, success
from dsync.config import FolderEntry, SyncMode
from dsync.state import AppState


def rm(
    ctx: typer.Context,
    id: Annotated[str, typer.Argument(help="Unique folder id")]
) -> None:
    """Remove a configured folder.."""
    state: AppState = ctx.obj

    filtered = list(filter(lambda f: f.id == id, state.folders.entries))
    e = filtered[0] if filtered else None

    if not e:
        error(f"The specified folder {id} is not configured.")
        return

    state.folders.entries.remove(e)

    state.folders.save(state.config_dir, overwrite=True)
    lines = [
        "Removed folder:",
        f"    • {e.id}",
        f"      path:    {e.path}",
        f"      mode:    {e.mode.value}",
    ]

    if e.devices is not None:
        lines.append(f"      devices: {', '.join(e.devices)}")

    success("\n".join(lines))

