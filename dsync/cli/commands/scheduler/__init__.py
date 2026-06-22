"""Top-level `scheduler` CLI group for the interval-sync daemon."""

from __future__ import annotations

import typer

from .disable import disable
from .enable import enable
from .run import run
from .status import status

app: typer.Typer = typer.Typer(
    help="Interval-sync scheduler daemon management commands", no_args_is_help=True
)

app.command()(enable)
app.command()(disable)
app.command()(status)
app.command()(run)
