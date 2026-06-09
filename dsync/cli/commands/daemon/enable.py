"""CLI command to enable daemon mode."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import typer

from dsync.cli.console import error, info, success, warn
from dsync.config.daemon import DaemonConfig
from dsync.daemon.manager import DaemonManager

if TYPE_CHECKING:
    from dsync.state import AppState


def enable(
    ctx: typer.Context,
    port: Annotated[int, typer.Option(help="Network port for daemon")] = 9999,
    cert: Annotated[str, typer.Option(help="Path to TLS certificate")] = "cert.pem",
    key: Annotated[str, typer.Option(help="Path to TLS private key")] = "key.pem",
) -> None:
    """Enable background daemon mode (server only).

    Creates systemd service on Linux for auto-start on boot and persistent operation.
    The daemon will listen for incoming sync connections from peers.

    Args:
        ctx: Typer context containing AppState
        port: Network port for the daemon to listen on
        cert: Path to TLS certificate file
        key: Path to TLS private key file
    """
    state: AppState = ctx.obj

    try:
        manager = DaemonManager.get_manager(state.config_dir)

        if manager.is_enabled():
            warn("Daemon is already enabled")
            return

        info(f"Enabling daemon on port {port}...")
        manager.enable(port=port, cert=cert, key=key)

        daemon_config = DaemonConfig(enabled=True, port=port, cert=cert, key=key)
        daemon_config.save(state.config_dir, overwrite=True)

        success("Daemon enabled and started")
        success("Service will auto-start on boot")
        info("Check status with: dsync daemon status")
        info("Stop with: dsync daemon disable")

    except NotImplementedError as e:
        error(str(e))
        raise typer.Exit(code=1) from None
    except PermissionError:
        error("Permission denied. Daemon setup requires sudo privileges")
        raise typer.Exit(code=1) from None
    except Exception as e:
        error(f"Failed to enable daemon: {e}")
        raise typer.Exit(code=1) from None
