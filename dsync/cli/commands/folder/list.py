"""CLI command to list configured folders."""

from __future__ import annotations

import typer  # noqa: TC002

from dsync.cli.console import success


def list(ctx: typer.Context) -> None:  # noqa: A001
    """List all configured folders."""
    entries = ctx.obj.folders.entries

    if not entries:
        success("There are currently no folders configured.")
        return

    lines = ["Current Folder Configuration:"]
    for e in entries:
        lines.append(f"    • {e.id}")
        lines.append(f"      path:    {e.path}")
        lines.append(f"      mode:    {e.mode.value}")
        lines.append(f"      devices: {', '.join(e.devices)}")
        if e.interval is not None:
            lines.append(f"      interval: {e.interval}")

    success("\n".join(lines))
