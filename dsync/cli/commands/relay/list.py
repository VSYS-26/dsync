"""CLI command to list configured relay servers."""

from __future__ import annotations

import typer  # noqa: TC002

from dsync.cli.console import success


def list(ctx: typer.Context) -> None:
    """List all configured relay servers."""
    relays = ctx.obj.relays.relays

    if not relays:
        success("There are currently no relay servers configured.")
        return

    lines = ["Configured Relay Servers:"]
    for e in relays:
        lines.append(f"    • {e.id}")
        lines.append(f"      address:     {e.host}:{e.port}")
        lines.append(f"      fingerprint: {e.fingerprint}")

    success("\n".join(lines))
