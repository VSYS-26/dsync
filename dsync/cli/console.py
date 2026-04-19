from rich.console import Console
from rich.theme import Theme

_theme = Theme(
    {
        "info": "cyan",
        "success": "green",
        "warn": "yellow",
        "error": "bold red",
    }
)

console: Console = Console(theme=_theme)


def info(message: str) -> None:
    console.print(message, style="info")


def success(message: str) -> None:
    console.print(message, style="success")


def warn(message: str) -> None:
    console.print(message, style="warn")


def error(message: str) -> None:
    console.print(message, style="error")
