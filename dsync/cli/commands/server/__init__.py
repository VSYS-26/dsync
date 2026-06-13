"""Top-level `server` CLI group for the sync server daemon."""

from __future__ import annotations

import typer

from .disable import disable
from .enable import enable
from .status import status

app: typer.Typer = typer.Typer(help="Sync server daemon management commands", no_args_is_help=True)

app.command()(enable)
app.command()(disable)
app.command()(status)
