"""CLI command to list trusted devices."""

from __future__ import annotations

import typer

from dsync.cli.console import success


def list(ctx: typer.Context) -> None:
    """List all trusted devices."""
    devices = ctx.obj.devices.trusted_devices

    if not devices:
        success("There are currently no trusted devices configured.")
        return

    lines = ["Currently Trusted Devices:"]
    for e in devices:
        lines.append(f"    • {e.id}")
        lines.append(f"      fingerprint: {e.fingerprint}")
        lines.append(f"      relay_id:    {e.relay_id}")

    success("\n".join(lines))
