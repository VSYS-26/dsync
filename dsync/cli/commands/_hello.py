from typing import Annotated

import typer

from dsync.cli.console import success


def hello(
    name: Annotated[str, typer.Argument(help="Name to greet")] = "world",
) -> None:
    """Greet someone by name."""
    success(f"Hello, {name}!")
