import typer

from dsync.cli.commands._demo import add, list

app: typer.Typer = typer.Typer(
    help="Demo subcommand group - template for later used commands 'folder', 'peer' etc.",
    no_args_is_help=True,
)

app.add_typer(add.app)
app.add_typer(list.app)
