"""CLI command to check daemon status."""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

from dsync.cli.console import error, info
from dsync.daemon.manager import DaemonManager

if TYPE_CHECKING:
    from dsync.state import AppState


def status(ctx: typer.Context) -> None:
    """Check daemon status.

    Shows whether the daemon is enabled and if it's currently running.

    Args:
        ctx: Typer context containing AppState
    """
    state: AppState = ctx.obj

    try:
        manager = DaemonManager.get_manager(state.config_dir)

        enabled = manager.is_enabled()
        running = manager.is_running() if enabled else False

        status_text = "[green]✓ Enabled[/green]" if enabled else "[yellow]✗ Disabled[/yellow]"

        running_text = "[green]✓ Running[/green]" if running else "[yellow]✗ Stopped[/yellow]"

        info(f"Daemon Status: {status_text}")
        info(f"Service Running: {running_text}")

        if enabled:
            port = state.daemon.port
            info(f"Port: {port}")
            info(f"Config Dir: {state.config_dir}")

    except NotImplementedError as e:
        error(str(e))
        raise typer.Exit(code=1) from None
    except Exception as e:
        error(f"Failed to check daemon status: {e}")
        raise typer.Exit(code=1) from None
