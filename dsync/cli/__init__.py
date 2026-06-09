"""CLI root Typer app."""

import typer

from dsync.cli.callbacks.config_dir import config_dir
from dsync.cli.commands import _demo, daemon, device, folder, peer, sync
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
cli.add_typer(peer.app, name="peer")
cli.add_typer(device.app, name="device")
cli.add_typer(folder.app, name="folder")
cli.add_typer(daemon.app, name="daemon")
