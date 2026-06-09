"""CLI command to disable daemon mode."""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

from dsync.cli.console import error, info, success
from dsync.config.daemon import DaemonConfig
from dsync.daemon.manager import DaemonManager

if TYPE_CHECKING:
    from dsync.state import AppState


def disable(ctx: typer.Context) -> None:
    """Disable background daemon mode.

    Stops the systemd service and removes it from auto-start.
    The daemon will no longer run in the background.

    Args:
        ctx: Typer context containing AppState
    """
    state: AppState = ctx.obj

    try:
        manager = DaemonManager.get_manager(state.config_dir)

        if not manager.is_enabled():
            info("Daemon is not enabled")
            return

        info("Disabling daemon...")
        manager.disable()

        daemon_config = DaemonConfig(enabled=False)
        daemon_config.save(state.config_dir, overwrite=True)

        success("Daemon disabled and stopped")
        info("Service will no longer auto-start")

    except NotImplementedError as e:
        error(str(e))
        raise typer.Exit(code=1) from None
    except PermissionError:
        error("Permission denied. Daemon removal requires sudo privileges")
        raise typer.Exit(code=1) from None
    except Exception as e:
        error(f"Failed to disable daemon: {e}")
        raise typer.Exit(code=1) from None
