"""Demo subcommand group used as a template for real commands."""

import typer

from dsync.cli.commands._demo import add, list as list_cmd

app: typer.Typer = typer.Typer(
    help="Demo subcommand group - template for later used commands 'folder', 'peer' etc.",
    no_args_is_help=True,
)

app.add_typer(add.app)
app.add_typer(list_cmd.app)
