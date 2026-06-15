"""Generic CLI operations shared by every daemon (enable/disable/status).

Holds the shared console output, error handling and exit codes, so each daemon's
command group stays a thin wrapper.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from collections.abc import Callable

    from dsync.daemon.base import Daemon


def run_enable(daemon: Daemon, label: str, save_config: Callable[[], None]) -> None:
    """Install, start and persist a daemon with uniform messages and errors.

    Args:
        daemon: The daemon to enable.
        label: Display/command name (e.g. ``server``).
        save_config: Callable persisting the daemon's enabled config.
    """
    # Deferred import to avoid a circular import at module load.
    from dsync.cli.console import error, info, success, warn

    try:
        if daemon.is_enabled():
            warn(f"{label} daemon is already enabled")
            return

        info(f"Enabling {label} daemon...")
        daemon.enable()
        save_config()

        success(f"{label} daemon enabled and started")
        success("Service will auto-start on boot")
        info(f"Check status with: dsync {label} status")
        info(f"Stop with: dsync {label} disable")

    except NotImplementedError as e:
        error(str(e))
        raise typer.Exit(code=1) from None
    except PermissionError:
        error("Permission denied. Daemon setup requires elevated privileges (e.g. sudo on Linux)")
        raise typer.Exit(code=1) from None
    except Exception as e:
        error(f"Failed to enable {label} daemon: {e}")
        raise typer.Exit(code=1) from None


def run_disable(daemon: Daemon, label: str, save_config: Callable[[], None]) -> None:
    """Stop and remove a daemon with uniform messages and errors.

    Args:
        daemon: The daemon to disable.
        label: Display/command name (e.g. ``server``).
        save_config: Callable persisting the daemon's disabled config.
    """
    from dsync.cli.console import error, info, success

    try:
        if not daemon.is_enabled():
            info(f"{label} daemon is not enabled")
            return

        info(f"Disabling {label} daemon...")
        daemon.disable()
        save_config()

        success(f"{label} daemon disabled and stopped")
        info("Service will no longer auto-start")

    except NotImplementedError as e:
        error(str(e))
        raise typer.Exit(code=1) from None
    except PermissionError:
        error("Permission denied. Daemon removal requires elevated privileges (e.g. sudo on Linux)")
        raise typer.Exit(code=1) from None
    except Exception as e:
        error(f"Failed to disable {label} daemon: {e}")
        raise typer.Exit(code=1) from None


def run_status(daemon: Daemon, label: str, extra_info: Callable[[], None] | None = None) -> None:
    """Print enabled/running state for a daemon, plus optional extra lines.

    Args:
        daemon: The daemon to inspect.
        label: Display/command name (e.g. ``server``).
        extra_info: Optional callable printing daemon-specific status lines,
            invoked only when the daemon is enabled.
    """
    from dsync.cli.console import error, info

    try:
        enabled = daemon.is_enabled()
        running = daemon.is_running() if enabled else False

        status_text = "[green]Enabled[/green]" if enabled else "[yellow]Disabled[/yellow]"
        running_text = "[green]Running[/green]" if running else "[yellow]Stopped[/yellow]"

        info(f"{label} daemon: {status_text}")
        info(f"Service Running: {running_text}")

        if enabled and extra_info is not None:
            extra_info()

    except NotImplementedError as e:
        error(str(e))
        raise typer.Exit(code=1) from None
    except Exception as e:
        error(f"Failed to check {label} daemon status: {e}")
        raise typer.Exit(code=1) from None
