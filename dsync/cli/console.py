"""Themed Rich console and severity-tagged print helpers."""

from rich.console import Console
from rich.panel import Panel
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
    """Print ``message`` styled as informational output."""
    console.print(message, style="info")


def success(message: str) -> None:
    """Print ``message`` styled as success output."""
    console.print(message, style="success")


def warn(message: str) -> None:
    """Print ``message`` styled as a warning."""
    console.print(message, style="warn")


def error(message: str) -> None:
    """Print ``message`` styled as an error."""
    console.print(message, style="error")


_BANNER = r"""
   __
  / /____ __ _____  ____
 / __/ _ // // / _ \/ __/
 \__/\_,_/ //_/_//_/\__/
       /___/  dsync
"""


def welcome(role: str, *, port: int | None = None, host: str | None = None) -> None:
    """Print a startup banner identifying the running dsync process.

    Args:
        role: Short label for the process (e.g. ``"server"``, ``"client"``,
            ``"scheduler"``).
        port: Optional port the process is bound to.
        host: Optional host the process targets or listens on.
    """
    lines = [f"[success]{_BANNER.rstrip()}[/success]", "", f"[info]Role:[/info] {role}"]
    if host is not None:
        lines.append(f"[info]Host:[/info] {host}")
    if port is not None:
        lines.append(f"[info]Port:[/info] {port}")
    console.print(Panel.fit("\n".join(lines), title="dsync starting", border_style="success"))
    print_help()


def print_help() -> None:
    """Print a concise help reminder for users running the daemon foreground."""
    console.print(
        Panel.fit(
            "[info]Useful commands:[/info]\n"
            "  [success]dsync --help[/success]            Show all CLI commands\n"
            "  [success]dsync sync start --help[/success]   Sync server/client options\n"
            "  [success]dsync server status[/success]       Check server daemon status\n"
            "  [success]dsync scheduler status[/success]    Check scheduler daemon status\n"
            "[info]Press Ctrl+C to stop.[/info]",
            title="help",
            border_style="info",
        )
    )
