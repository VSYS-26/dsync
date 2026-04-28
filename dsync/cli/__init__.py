"""CLI root Typer app."""

import typer

from dsync.cli.callbacks.config_dir import config_dir
from dsync.cli.commands import _demo, sync
from dsync.cli.commands._hello import hello

cli: typer.Typer = typer.Typer(
    name="dsync",
    help="Decentralized file sync between trusted devices.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# callbacks
cli.callback()(config_dir)

# commands
cli.command()(hello)
cli.add_typer(_demo.app, name="demo")
cli.add_typer(sync.app, name="sync")
