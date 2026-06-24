"""Top-level `device` CLI group exposing device management commands."""

from __future__ import annotations

import typer

from .add import add
from .list import list
from .mod import mod
from .rm import rm

app: typer.Typer = typer.Typer(help="Device management commands", no_args_is_help=True)

app.command()(list)
app.command()(add)
app.command()(rm)
app.command()(mod)
