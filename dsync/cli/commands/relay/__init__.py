"""Top-level `relay` CLI group exposing relay-server management commands."""

from __future__ import annotations

import typer

from .add import add
from .connect import connect
from .list import list
from .mod import mod
from .rm import rm
from .serve import serve

app: typer.Typer = typer.Typer(
    help="Relay-server management commands",
    no_args_is_help=True,
)

app.command()(list)
app.command()(add)
app.command()(rm)
app.command()(mod)
app.command()(serve)
app.command()(connect)
