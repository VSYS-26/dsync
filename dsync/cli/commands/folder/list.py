"""CLI command to list configured folders."""

from __future__ import annotations

import typer

from dsync.cli.console import success


def list(ctx: typer.Context) -> None:
    """List all configured folders."""
    entries = ctx.obj.folders.entries

    if not entries:
        success("There are currently no folders configured.")
        return

    lines = ["Current Folder Configuration:"]
    for e in entries:
        lines.append(
            f"""\
    • {e.id}
      path:    {e.path}
      mode:    {e.mode.value}
      devices: {", ".join(e.devices)}
    """
        )

    success("\n".join(lines))
