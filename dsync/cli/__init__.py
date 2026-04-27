import typer

from dsync.cli.commands import _demo
from dsync.cli.commands._hello import hello
from dsync.cli.commands import sync

cli: typer.Typer = typer.Typer(
    name="dsync",
    help="Decentralized file sync between trusted devices.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# commands
cli.command()(hello)
cli.add_typer(_demo.app, name="demo")
cli.add_typer(sync.app, name="sync")
