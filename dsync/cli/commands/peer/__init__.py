"""Top-level `peer` CLI group exposing announce/discover/map commands."""

from __future__ import annotations

import typer

from .announce import announce
from .discover import discover
from .map import show_map

app: typer.Typer = typer.Typer(help="Peer discovery commands", no_args_is_help=True)

app.command()(announce)
app.command()(discover)
app.command(name="map")(show_map)
