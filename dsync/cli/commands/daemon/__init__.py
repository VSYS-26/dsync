"""Top-level `daemon` CLI group exposing daemon management commands."""

from __future__ import annotations

import typer

from .disable import disable
from .enable import enable
from .status import status

app: typer.Typer = typer.Typer(help="Daemon management commands", no_args_is_help=True)

app.command()(enable)
app.command()(disable)
app.command()(status)
