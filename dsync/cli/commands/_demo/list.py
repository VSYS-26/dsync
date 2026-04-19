import typer

from dsync.cli.console import console

app: typer.Typer = typer.Typer()


@app.command("list")
def list_items() -> None:
    """List all items in the demo store."""
    console.print("[info]would list items[/info]")
