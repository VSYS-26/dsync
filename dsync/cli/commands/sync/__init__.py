"""Top-level `sync` CLI group exposing sync commands."""

from __future__ import annotations

import typer

from .run_backup import sync
from .start import start_p2p_sync

app: typer.Typer = typer.Typer(help="Sync commands", no_args_is_help=True)

app.command(name="start")(start_p2p_sync)
app.command(name="run_backup")(sync)
