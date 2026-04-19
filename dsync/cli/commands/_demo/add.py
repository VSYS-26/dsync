from typing import Annotated

import typer

from dsync.cli.console import console

app: typer.Typer = typer.Typer()


@app.command()
def add(item_id: Annotated[str, typer.Argument(help="Item ID")]) -> None:
    """Add an item to the demo store."""
    console.print(f"[info]would add:[/info] {item_id}")
